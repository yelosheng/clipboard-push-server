import time as pytime

from dotenv import set_key
from flask import request, jsonify, render_template, redirect, url_for, flash, send_from_directory, send_file, Response
from flask_login import current_user, login_user, login_required, logout_user
from werkzeug.security import generate_password_hash

from .services.pipeline_relay import PipelineDroppedToDisk


def register_routes(
    app,
    *,
    ADMIN_PASSWORD,
    User,
    get_serialized_sessions,
    os,
    logger,
    s3_client,
    R2_BUCKET_NAME,
    get_r2_bucket_usage,
    DASHBOARD_R2_BUCKET,
    empty_r2_bucket,
    debug_signal_log,
    CLIENT_SESSIONS,
    socketio,
    ALLOWED_ACTIVITY_TYPES,
    emit_activity_log,
    verify_password,
    PASSWORD_HASH_FILE,
    STORAGE_BACKEND,
    LOCAL_STORAGE_PATH,
    LOCAL_STORAGE_BASE_URL,
    local_write_file_stream,
    local_get_file_path,
    local_storage_get_usage,
    local_storage_clear,
    pipeline_registry,
    DOTENV_PATH,
    HISTORY_DB_PATH=None,
    history_query_summary=None,
    history_query_clients=None,
    history_query_hourly=None,
    history_query_daily=None,
    history_query_countries=None,
    fcm_db_path=None,
):
    @app.route('/login', methods=['GET', 'POST'])
    def login():
        if current_user.is_authenticated:
            return redirect(url_for('dashboard'))

        if request.method == 'POST':
            password = request.form.get('password')
            remember = True if request.form.get('remember') else False

            if verify_password(password):
                user = User('admin')
                login_user(user, remember=remember)
                return redirect(url_for('dashboard'))

            flash('Invalid password')

        return render_template('login.html')

    @app.route('/logout')
    @login_required
    def logout():
        logout_user()
        return redirect(url_for('login'))

    @app.route('/dashboard')
    @login_required
    def dashboard():
        return render_template('dashboard.html', client_sessions=get_serialized_sessions())

    @app.route('/')
    def index():
        if current_user.is_authenticated:
            return redirect(url_for('dashboard'))
        return "Clipboard Push Relay Server is Running (Port 5055). <a href='/login'>Login</a>"

    @app.route('/favicon.ico')
    def favicon():
        return send_from_directory(
            os.path.join(app.root_path, 'static'),
            'favicon.png',
            mimetype='image/vnd.microsoft.icon',
        )

    @app.route('/change_password', methods=['POST'])
    @login_required
    def change_password():
        current = request.form.get('current_password', '')
        new_pw = request.form.get('new_password', '')
        confirm = request.form.get('confirm_password', '')

        if not verify_password(current):
            flash('Current password is incorrect.')
            return redirect(url_for('dashboard'))

        if new_pw != confirm:
            flash('New password and confirmation do not match.')
            return redirect(url_for('dashboard'))

        if len(new_pw) < 8:
            flash('New password must be at least 8 characters.')
            return redirect(url_for('dashboard'))

        new_hash = generate_password_hash(new_pw)
        hash_dir = os.path.dirname(PASSWORD_HASH_FILE)
        os.makedirs(hash_dir, exist_ok=True)
        with open(PASSWORD_HASH_FILE, 'w', encoding='utf-8') as f:
            f.write(new_hash)

        logger.info('Admin password changed successfully.')
        flash('Password changed successfully.')
        return redirect(url_for('dashboard'))

    @app.route('/api/dashboard/r2_usage', methods=['GET'])
    @login_required
    def api_dashboard_r2_usage():
        if STORAGE_BACKEND == 'local':
            try:
                usage = local_storage_get_usage()
                usage['backend'] = 'local'
                usage['updated_at_epoch_ms'] = int(pytime.time() * 1000)
                return jsonify(usage)
            except Exception as e:
                logger.error(f"Failed to get local storage usage: {e}")
                return jsonify({'error': str(e)}), 500
        if not DASHBOARD_R2_BUCKET:
            return jsonify({'error': 'R2 not configured (DASHBOARD_R2_BUCKET is empty)'}), 503
        try:
            usage = get_r2_bucket_usage(DASHBOARD_R2_BUCKET)
            usage['backend'] = 'r2'
            usage['updated_at_epoch_ms'] = int(pytime.time() * 1000)
            return jsonify(usage)
        except Exception as e:
            logger.error(f"Failed to get R2 usage for dashboard: {e}")
            return jsonify({'error': str(e)}), 500

    @app.route('/api/dashboard/r2_empty', methods=['POST'])
    @login_required
    def api_dashboard_r2_empty():
        if STORAGE_BACKEND == 'local':
            try:
                result = local_storage_clear()
                usage = local_storage_get_usage()
                usage['backend'] = 'local'
                return jsonify({
                    'result': result,
                    'usage': usage,
                    'updated_at_epoch_ms': int(pytime.time() * 1000),
                })
            except Exception as e:
                logger.error(f"Failed to clear local storage: {e}")
                return jsonify({'error': str(e)}), 500
        if not DASHBOARD_R2_BUCKET:
            return jsonify({'error': 'R2 not configured (DASHBOARD_R2_BUCKET is empty)'}), 503
        try:
            result = empty_r2_bucket(DASHBOARD_R2_BUCKET)
            usage = get_r2_bucket_usage(DASHBOARD_R2_BUCKET)
            usage['backend'] = 'r2'
            return jsonify({
                'result': result,
                'usage': usage,
                'updated_at_epoch_ms': int(pytime.time() * 1000),
            })
        except Exception as e:
            logger.error(f"Failed to empty R2 bucket for dashboard: {e}")
            return jsonify({'error': str(e)}), 500

    @app.route('/api/file/upload_auth', methods=['POST'])
    def generate_upload_url():
        data = request.json
        filename = data.get('filename')
        content_type = data.get('content_type', 'application/octet-stream')

        if not filename:
            return jsonify({'error': 'Filename required'}), 400

        object_name = f"{int(pytime.time())}_{filename}"

        if STORAGE_BACKEND == 'local':
            base = LOCAL_STORAGE_BASE_URL.rstrip('/')
            return jsonify({
                'upload_url': f"{base}/api/file/upload/{object_name}",
                'download_url': f"{base}/api/file/download/{object_name}",
                'file_key': object_name,
                'expires_in': 300,
            })

        if s3_client is None:
            return jsonify({'error': 'File storage is not configured on this server'}), 503

        try:
            presigned_url = s3_client.generate_presigned_url(
                'put_object',
                Params={
                    'Bucket': R2_BUCKET_NAME,
                    'Key': object_name,
                    'ContentType': content_type,
                },
                ExpiresIn=300,
            )

            download_url = s3_client.generate_presigned_url(
                'get_object',
                Params={'Bucket': R2_BUCKET_NAME, 'Key': object_name},
                ExpiresIn=3600,
            )

            return jsonify({
                'upload_url': presigned_url,
                'download_url': download_url,
                'file_key': object_name,
                'expires_in': 300,
            })
        except Exception as e:
            logger.error(f"Error generating presigned URL: {e}")
            return jsonify({'error': str(e)}), 500

    @app.route('/api/file/upload/<path:file_key>', methods=['PUT'])
    def local_file_upload(file_key):
        if STORAGE_BACKEND != 'local':
            return jsonify({'error': 'Local storage not enabled'}), 404
        content_type = request.content_type or 'application/octet-stream'
        pipeline_buf = pipeline_registry.open_for_write(file_key, content_type)
        try:
            bytes_written = local_write_file_stream(
                LOCAL_STORAGE_PATH, file_key, request.stream, content_type,
                pipeline_buffer=pipeline_buf,
            )
            logger.info(f"Local upload: {file_key} ({bytes_written} bytes, streamed)")
            return '', 200
        except Exception as e:
            logger.error(f"Local upload failed: {e}")
            return jsonify({'error': str(e)}), 500

    @app.route('/api/file/download/<path:file_key>', methods=['GET'])
    def local_file_download(file_key):
        if STORAGE_BACKEND != 'local':
            return jsonify({'error': 'Local storage not enabled'}), 404

        if not request.headers.get('Range'):
            buf = pipeline_registry.get(file_key)
            if buf is None:
                buf = pipeline_registry.wait_for(file_key, timeout_s=5.0)
            if buf is not None and buf.attach_reader():
                content_type = buf.content_type
                def _stream():
                    try:
                        for chunk in buf.read_iter():
                            yield chunk
                    except (PipelineDroppedToDisk, TimeoutError, IOError) as e:
                        logger.warning(f"Pipeline relay {file_key} aborted: {e}")
                    finally:
                        buf.detach_reader()
                logger.info(f"Pipeline relay download: {file_key}")
                return Response(_stream(), content_type=content_type)
            if buf is not None and not buf.finished and not buf.failed:
                # Buffer exists but couldn't attach (dropped to disk); wait for PUT
                # to complete so the on-disk file is ready for send_file.
                buf.terminal_event.wait(timeout=120)

        file_path, content_type = local_get_file_path(LOCAL_STORAGE_PATH, file_key)
        if file_path is None:
            return jsonify({'error': 'File not found'}), 404
        return send_file(
            file_path,
            mimetype=content_type,
            as_attachment=False,
            conditional=True,
            max_age=0,
        )

    # Keys exposed in the settings UI (excludes FLASK_SECRET_KEY, ADMIN_PASSWORD)
    _SETTINGS_KEYS = [
        'STORAGE_BACKEND',
        'LOCAL_STORAGE_BASE_URL',
        'LOCAL_STORAGE_PATH',
        'R2_ACCOUNT_ID',
        'R2_ACCESS_KEY_ID',
        'R2_SECRET_ACCESS_KEY',
        'R2_BUCKET_NAME',
        'DASHBOARD_R2_BUCKET',
        'FLASK_DEBUG',
        'FIREBASE_CREDENTIALS_PATH',
    ]
    _SECRET_KEYS = {'R2_SECRET_ACCESS_KEY'}

    import os as _os

    @app.route('/api/settings', methods=['GET'])
    @login_required
    def get_settings():
        values = {}
        for key in _SETTINGS_KEYS:
            val = _os.environ.get(key, '')
            if key in _SECRET_KEYS and val:
                values[key] = val[:4] + '*' * max(0, len(val) - 4)
            else:
                values[key] = val
        return jsonify(values)

    @app.route('/api/settings', methods=['POST'])
    @login_required
    def save_settings():
        data = request.json or {}
        saved = []
        # Keys that should not be saved as empty (keep default instead)
        _REQUIRED_IF_SET = {'LOCAL_STORAGE_PATH', 'LOCAL_STORAGE_BASE_URL',
                            'R2_ACCOUNT_ID', 'R2_ACCESS_KEY_ID', 'R2_BUCKET_NAME',
                            'DASHBOARD_R2_BUCKET'}
        try:
            for key in _SETTINGS_KEYS:
                if key not in data:
                    continue
                val = str(data[key]).strip()
                if key in _SECRET_KEYS and set(val[4:]) == {'*'}:
                    continue
                # Don't persist empty values for important fields — let defaults apply
                if not val and key in _REQUIRED_IF_SET:
                    continue
                set_key(DOTENV_PATH, key, val)
                saved.append(key)
            logger.info(f'Settings updated via dashboard: {saved}')
            return jsonify({'saved': saved, 'restart_required': True})
        except PermissionError:
            msg = f'Permission denied writing {DOTENV_PATH}. Run: chmod 664 {DOTENV_PATH}'
            logger.error(msg)
            return jsonify({'error': msg}), 500
        except Exception as e:
            logger.error(f'Failed to save settings: {e}')
            return jsonify({'error': str(e)}), 500

    @app.route('/api/restart', methods=['POST'])
    @login_required
    def restart_server():
        import os, signal, threading
        def _kill():
            import time
            time.sleep(0.4)  # allow response to be sent first
            os.kill(os.getppid(), signal.SIGTERM)
        threading.Thread(target=_kill, daemon=True).start()
        logger.info('Server restart requested via dashboard')
        return jsonify({'status': 'restarting'})

    @app.route('/api/relay', methods=['POST'])
    def relay_message():
        try:
            content = request.json
            room = content.get('room')
            event = content.get('event')
            data = content.get('data')
            sender_id = content.get('sender_id') or content.get('client_id')

            debug_signal_log('http_rx', content, room=room, event=event, sender=sender_id, sid='http')

            if not room or not event or data is None:
                return jsonify({'error': 'Missing room, event, or data'}), 400

            # Assign a shared message id/timestamp (written back into data) so
            # the socket broadcast and the FCM message dedup as one on the
            # client. Must happen before the broadcast below.
            if event == 'clipboard_sync' and isinstance(data, dict):
                from .socket_events import build_clipboard_fcm_payload
                build_clipboard_fcm_payload(data)

            skip_sids = []
            if sender_id and sender_id in CLIENT_SESSIONS:
                skip_sids = list(CLIENT_SESSIONS[sender_id])
                logger.info(f"Skipping sids for sender {sender_id}: {skip_sids}")

            if skip_sids:
                socketio.emit(event, data, room=room, skip_sid=skip_sids)
            else:
                socketio.emit(event, data, room=room)

            # Fan out over FCM so a frozen/backgrounded peer still receives the
            # text even though this sender relayed over HTTP (Win32, quick-push).
            from .socket_events import fanout_relay_fcm
            fanout_relay_fcm(fcm_db_path, event, room, sender_id, data)

            debug_signal_log('http_tx', data, room=room, event=event, sender=sender_id or 'API', sid='http')
            logger.info(f"Relayed HTTP message to room {room}: event={event}, skipped={len(skip_sids)}")

            activity_type = event if event in ALLOWED_ACTIVITY_TYPES else 'api_relay'
            emit_activity_log(activity_type, room, sender_id or 'API', f"Event: {event}")

            return jsonify({'status': 'ok'}), 200
        except Exception as e:
            logger.error(f"Relay error: {e}")
            return jsonify({'error': str(e)}), 500

    @app.route('/history')
    @login_required
    def history_page():
        return render_template('history.html')

    @app.route('/api/history/summary')
    @login_required
    def api_history_summary():
        if not HISTORY_DB_PATH:
            return jsonify({'error': 'history not configured'}), 503
        return jsonify(history_query_summary(HISTORY_DB_PATH))

    @app.route('/api/history/clients')
    @login_required
    def api_history_clients():
        if not HISTORY_DB_PATH:
            return jsonify({'error': 'history not configured'}), 503
        search = request.args.get('search', '').strip()
        limit = min(int(request.args.get('limit', 100)), 500)
        offset = int(request.args.get('offset', 0))
        rows, total = history_query_clients(HISTORY_DB_PATH, search=search, limit=limit, offset=offset)
        return jsonify({'clients': rows, 'total': total})

    @app.route('/api/history/hourly')
    @login_required
    def api_history_hourly():
        if not HISTORY_DB_PATH:
            return jsonify({'error': 'history not configured'}), 503
        return jsonify(history_query_hourly(HISTORY_DB_PATH))

    @app.route('/api/history/daily')
    @login_required
    def api_history_daily():
        if not HISTORY_DB_PATH:
            return jsonify({'error': 'history not configured'}), 503
        days = int(request.args.get('days', 30))
        return jsonify(history_query_daily(HISTORY_DB_PATH, days=days))

    @app.route('/api/history/countries')
    @login_required
    def api_history_countries():
        if not HISTORY_DB_PATH:
            return jsonify({'error': 'history not configured'}), 503
        return jsonify(history_query_countries(HISTORY_DB_PATH))
