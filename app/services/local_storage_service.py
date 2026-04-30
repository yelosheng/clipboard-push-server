import glob
import json
import os
import time


def _human_readable(size_bytes):
    b = float(size_bytes)
    for unit in ('B', 'KB', 'MB', 'GB'):
        if b < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} TB"


def ensure_storage_dir(path):
    os.makedirs(path, exist_ok=True)


DEFAULT_CHUNK_SIZE = 64 * 1024  # 64 KB


def make_file_key(filename):
    return f"{int(time.time() * 1000)}_{filename}"


def write_file_stream(storage_path, file_key, stream, content_type, pipeline_buffer=None, chunk_size=DEFAULT_CHUNK_SIZE):
    """Stream `stream` to disk chunk by chunk. Writes to a .part file and atomically
    renames on success so readers never see a half-written file. Returns bytes written.

    If pipeline_buffer is provided, each chunk is also fed into it so a concurrent
    GET can stream the bytes without waiting for PUT to finish."""
    ensure_storage_dir(storage_path)
    final_path = os.path.join(storage_path, file_key)
    part_path = final_path + '.part'
    bytes_written = 0
    try:
        with open(part_path, 'wb') as f:
            while True:
                chunk = stream.read(chunk_size)
                if not chunk:
                    break
                f.write(chunk)
                bytes_written += len(chunk)
                if pipeline_buffer is not None:
                    pipeline_buffer.append(chunk)
        os.replace(part_path, final_path)
    except Exception as e:
        try:
            os.remove(part_path)
        except OSError:
            pass
        if pipeline_buffer is not None:
            pipeline_buffer.mark_failed(str(e))
        raise

    with open(final_path + '.meta', 'w') as f:
        json.dump({'content_type': content_type, 'created_at': time.time()}, f)

    if pipeline_buffer is not None:
        pipeline_buffer.mark_done()
    return bytes_written


def get_file_path(storage_path, file_key):
    """Returns (absolute_file_path, content_type) or (None, None) if not found.
    Caller hands the path to send_file() for streaming download."""
    file_path = os.path.join(storage_path, file_key)
    if not os.path.exists(file_path):
        return None, None
    content_type = 'application/octet-stream'
    meta_path = file_path + '.meta'
    if os.path.exists(meta_path):
        try:
            with open(meta_path) as f:
                content_type = json.load(f).get('content_type', content_type)
        except Exception:
            pass
    return file_path, content_type


def get_local_storage_usage(storage_path):
    """Returns storage stats dict compatible with R2 usage response format."""
    if not storage_path or not os.path.isdir(storage_path):
        return {
            'bucket': storage_path or '(not configured)',
            'objects_count': 0,
            'total_bytes': 0,
            'total_human': '0 B',
            'scanned_objects': 0,
        }
    total_bytes = 0
    objects_count = 0
    for entry in os.scandir(storage_path):
        if entry.is_file() and not entry.name.endswith('.meta'):
            total_bytes += entry.stat().st_size
            objects_count += 1
    return {
        'bucket': storage_path,
        'objects_count': objects_count,
        'total_bytes': total_bytes,
        'total_human': _human_readable(total_bytes),
        'scanned_objects': objects_count,
    }


def clear_storage(storage_path):
    """Delete all files in storage. Returns {'deleted_objects': n, 'reclaimed_human': '...'}."""
    if not storage_path or not os.path.isdir(storage_path):
        return {'deleted_objects': 0, 'reclaimed_human': '0 B'}
    deleted = 0
    reclaimed = 0
    for entry in os.scandir(storage_path):
        if entry.is_file():
            try:
                size = entry.stat().st_size
                os.remove(entry.path)
                if not entry.name.endswith('.meta'):
                    deleted += 1
                    reclaimed += size
            except Exception:
                pass
    return {'deleted_objects': deleted, 'reclaimed_human': _human_readable(reclaimed)}


def purge_old_files(storage_path, max_age_s=3600):
    """Delete files older than max_age_s seconds. Returns count of deleted files.
    Also sweeps orphaned .part files (interrupted uploads) by mtime."""
    if not os.path.isdir(storage_path):
        return 0
    now = time.time()
    deleted = 0
    for meta_path in glob.glob(os.path.join(storage_path, '*.meta')):
        try:
            with open(meta_path) as f:
                created_at = json.load(f).get('created_at', 0)
            if now - created_at > max_age_s:
                file_key = os.path.basename(meta_path)[:-5]  # strip .meta
                data_path = os.path.join(storage_path, file_key)
                if os.path.exists(data_path):
                    os.remove(data_path)
                    deleted += 1
                os.remove(meta_path)
        except Exception:
            pass
    for part_path in glob.glob(os.path.join(storage_path, '*.part')):
        try:
            if now - os.path.getmtime(part_path) > max_age_s:
                os.remove(part_path)
        except Exception:
            pass
    return deleted
