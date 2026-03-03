from flask import request
from flask_socketio import emit, join_room, leave_room


def register_socket_events(
    socketio,
    *,
    logger,
    CLIENT_SESSIONS,
    detach_sid_from_tracking,
    get_serialized_sessions,
    normalize_client_type,
    get_all_room_states,
    CLIENT_TYPES,
    CLIENT_DEVICE_NAMES,
    CLIENT_LAST_SEEN_MS,
    current_time_ms,
    CLIENT_JOINED_AT_MS,
    update_client_network_meta,
    update_client_probe_meta,
    CLIENT_ROOMS,
    remove_client_from_room_order,
    ROOM_LAST_PROBE,
    broadcast_room_stats,
    emit_room_state_changed,
    ROOM_CLIENT_ORDER,
    enforce_room_capacity,
    trigger_lan_probe_if_ready,
    get_client_from_sid,
    CLIENT_NETWORK_META,
    emit_activity_log,
    PENDING_LAN_PROBES,
    parse_signal_payload,
    resolve_signal_context,
    debug_signal_log,
    ensure_protocol_version,
    is_sender_authorized_for_room,
    get_or_create_transfer_context,
    get_room_lan_state,
    instruct_upload_relay,
    update_transfer_state,
    transfer_decision_timeout_worker,
    TRANSFER_CONTEXTS,
    instruct_finish,
):
    @socketio.on('connect')
    def on_connect():
        logger.info(f"Client connected: {request.sid}")
        socketio.emit('server_stats', {'clients': len(CLIENT_SESSIONS), 'msg': 'New connection'}, room='dashboard_room')

    @socketio.on('disconnect')
    def on_disconnect():
        logger.info(f"Client disconnected: {request.sid}")
        removed_client = detach_sid_from_tracking(request.sid, reason='peer_disconnected')
        if removed_client:
            logger.info(f"Removed SID {request.sid} from client {removed_client}")
            socketio.emit('client_list_update', get_serialized_sessions(), room='dashboard_room')
        socketio.emit('server_stats', {'clients': len(CLIENT_SESSIONS), 'msg': 'Client disconnected'}, room='dashboard_room')

    @socketio.on('client_ping')
    def on_client_ping():
        emit('server_pong')

    @socketio.on('join')
    def on_join(data):
        payload = data if isinstance(data, dict) else {}
        room = payload.get('room')
        client_id = payload.get('client_id')
        client_type = normalize_client_type(payload.get('client_type'))

        if room:
            join_room(room)
            emit('status', {'msg': f'Joined room: {room}'}, room=room)
            logger.info(f"Client {request.sid} joined room: {room}")

            if room == 'dashboard_room':
                serialized_sessions = get_serialized_sessions()
                logger.info(f"Dashboard joined. Sending immediate update to {request.sid}: {serialized_sessions}")
                emit('client_list_update', serialized_sessions, room=request.sid)
                emit('room_states_snapshot', {'rooms': get_all_room_states()}, room=request.sid)

        if not client_id:
            return

        if not client_type:
            logger.warning(f"Client {request.sid} missing client_type for client_id {client_id}")
            emit('error', {'code': 'E_BAD_SCHEMA', 'msg': 'client_type is required when providing client_id'})
            return

        if client_id not in CLIENT_SESSIONS:
            CLIENT_SESSIONS[client_id] = set()
        CLIENT_SESSIONS[client_id].add(request.sid)

        CLIENT_TYPES[client_id] = client_type
        raw_device_name = payload.get('device_name')
        parsed_device_name = raw_device_name.strip() if isinstance(raw_device_name, str) else ''
        if parsed_device_name:
            CLIENT_DEVICE_NAMES[client_id] = parsed_device_name
        elif client_id not in CLIENT_DEVICE_NAMES:
            CLIENT_DEVICE_NAMES[client_id] = client_id

        logger.info("Join metadata: sid=%s room=%s client_id=%s client_type=%s device_name=%s",
                    request.sid, room, client_id, client_type, CLIENT_DEVICE_NAMES.get(client_id, client_id))
        CLIENT_LAST_SEEN_MS[client_id] = current_time_ms()
        CLIENT_JOINED_AT_MS.setdefault(client_id, current_time_ms())

        network_data = payload.get('network')
        if isinstance(network_data, dict):
            update_client_network_meta(client_id, network_data)

        probe_data = payload.get('probe')
        if isinstance(probe_data, dict):
            update_client_probe_meta(client_id, probe_data)

        if room:
            old_room = CLIENT_ROOMS.get(client_id)
            if old_room and old_room != room:
                remove_client_from_room_order(client_id, old_room)
                ROOM_LAST_PROBE.pop(old_room, None)
                broadcast_room_stats(old_room)
                emit_room_state_changed(old_room, reason='peer_moved')

            CLIENT_ROOMS[client_id] = room
            room_clients = ROOM_CLIENT_ORDER.setdefault(room, [])
            if client_id not in room_clients:
                room_clients.append(client_id)

            enforce_room_capacity(room)
            broadcast_room_stats(room)
            emit_room_state_changed(room, reason='peer_joined')
            trigger_lan_probe_if_ready(room, reason='peer_joined')
        else:
            logger.warning(f"Client {client_id} joined without room info in payload")

        logger.info(f"Registered client_id {client_id} with sid {request.sid}. Current Rooms: {CLIENT_ROOMS}")
        socketio.emit('client_list_update', get_serialized_sessions(), room='dashboard_room')

    @socketio.on('leave')
    def on_leave(data):
        payload = data if isinstance(data, dict) else {}
        room = payload.get('room')
        if room:
            leave_room(room)
            emit('status', {'msg': f'Left room: {room}'}, room=room)
            logger.info(f"Client left room: {room}")
            removed_client = detach_sid_from_tracking(request.sid, reason='peer_left_room', room_hint=room)
            if removed_client:
                socketio.emit('client_list_update', get_serialized_sessions(), room='dashboard_room')
            broadcast_room_stats(room)
            emit_room_state_changed(room, reason='peer_left_room')

    @socketio.on('peer_network_update')
    def on_peer_network_update(data):
        payload = data if isinstance(data, dict) else {}
        room = payload.get('room')
        client_id = payload.get('client_id') or get_client_from_sid(request.sid)
        if client_id == 'Unknown':
            emit('error', {'code': 'E_ROLE_DENIED', 'msg': 'client_id cannot be resolved for peer_network_update'})
            return

        if room and CLIENT_ROOMS.get(client_id) != room:
            emit('error', {'code': 'E_TRANSFER_STATE', 'msg': 'client does not belong to the specified room'})
            return

        update_client_network_meta(client_id, payload.get('network'))
        CLIENT_LAST_SEEN_MS[client_id] = current_time_ms()

        target_room = room or CLIENT_ROOMS.get(client_id)
        if target_room:
            ROOM_LAST_PROBE.pop(target_room, None)
            emit_room_state_changed(target_room, reason='network_updated')
            trigger_lan_probe_if_ready(target_room, reason='network_updated')

            epoch = CLIENT_NETWORK_META.get(client_id, {}).get('network_epoch', 0)
            emit_activity_log('peer_network_update', target_room, client_id, f"network_epoch={epoch}")

    @socketio.on('lan_probe_result')
    def on_lan_probe_result(data):
        payload = data if isinstance(data, dict) else {}
        room = payload.get('room')
        probe_id = payload.get('probe_id')
        result = str(payload.get('result', '')).strip().lower()

        if not room or not probe_id:
            emit('error', {'code': 'E_BAD_SCHEMA', 'msg': 'room and probe_id are required for lan_probe_result'})
            return

        pending = PENDING_LAN_PROBES.get(probe_id)
        if not pending or pending.get('room') != room:
            emit('error', {'code': 'E_PROBE_STALE', 'msg': 'probe_id is unknown or stale'})
            return

        if pending.get('resolved'):
            return

        normalized_result = result if result in {'ok', 'fail', 'timeout'} else 'fail'
        pending['resolved'] = True

        ROOM_LAST_PROBE[room] = {
            'probe_id': probe_id,
            'status': normalized_result,
            'latency_ms': payload.get('latency_ms'),
            'checked_at_ms': current_time_ms(),
            'reason': payload.get('reason', '')
        }

        PENDING_LAN_PROBES.pop(probe_id, None)

        sender = get_client_from_sid(request.sid)
        emit_activity_log('lan_probe_result', room, sender, f"{probe_id}: {normalized_result}")
        emit_room_state_changed(room, reason='probe_result')

    @socketio.on('clipboard_push')
    def handle_clipboard_push(data):
        room = data.get('room')
        if room:
            emit('clipboard_sync', data, room=room, include_self=False)
            logger.info(f"Relayed clipboard data to room: {room}")

            sender = get_client_from_sid(request.sid)
            content_preview = data.get('content', '')[:30] + '...' if data.get('content') else 'Encrypted Data'
            socketio.emit('activity_log', {
                'type': 'clipboard',
                'room': room,
                'sender': sender,
                'content': content_preview
            }, room='dashboard_room')

    @socketio.on('file_push')
    def handle_file_push(data):
        room = data.get('room')
        if room:
            emit('file_sync', data, room=room, include_self=False)
            logger.info(f"Relayed file metadata to room: {room}")

            sender = get_client_from_sid(request.sid)
            filename = data.get('filename', 'Unknown File')
            emit_activity_log('file', room, sender, filename)

    @socketio.on('file_announcement')
    def handle_file_announcement(data):
        room = data.get('room') if isinstance(data, dict) else None
        if room:
            emit('file_announcement', data, room=room, include_self=False)
            logger.info(f"Relayed file_announcement to room: {room}")

            sender = get_client_from_sid(request.sid)
            payload = parse_signal_payload(data)
            filename = payload.get('filename', 'Unknown File')
            file_id = payload.get('file_id', 'Unknown ID')
            emit_activity_log('file_announcement', room, sender, f"{filename} ({file_id})")

    @socketio.on('file_ack')
    def handle_file_ack(data):
        room = data.get('room') if isinstance(data, dict) else None
        if room:
            emit('file_ack', data, room=room, include_self=False)
            logger.info(f"Relayed file_ack to room: {room}")

            sender = get_client_from_sid(request.sid)
            payload = parse_signal_payload(data)
            file_id = payload.get('file_id', 'Unknown ID')
            method = payload.get('method', 'unknown')
            emit_activity_log('file_ack', room, sender, f"{file_id} via {method}")

    @socketio.on('file_request_relay')
    def handle_file_request_relay(data):
        room = data.get('room') if isinstance(data, dict) else None
        if room:
            emit('file_request_relay', data, room=room, include_self=False)
            logger.info(f"Relayed file_request_relay to room: {room}")

            sender = get_client_from_sid(request.sid)
            payload = parse_signal_payload(data)
            file_id = payload.get('file_id', 'Unknown ID')
            reason = payload.get('reason', 'unspecified')
            emit_activity_log('file_request_relay', room, sender, f"{file_id}: {reason}")

    @socketio.on('file_available')
    def handle_file_available(data):
        room, payload = resolve_signal_context(data)
        sender = get_client_from_sid(request.sid)
        debug_signal_log('rx', data, room=room, event='file_available', sender=sender)

        if not room:
            logger.warning(f"Dropped file_available due to missing room. sid={request.sid}, data={data}")
            return

        if not ensure_protocol_version(payload, 'file_available'):
            return

        if not is_sender_authorized_for_room(sender, room):
            logger.warning(f"Rejected file_available from unauthorized sender={sender} room={room} sid={request.sid}")
            debug_signal_log('drop', payload, room=room, event='file_available', sender=sender)
            emit('error', {'code': 'E_ROLE_DENIED', 'msg': 'sender is not authorized for this room'}, room=request.sid)
            return

        context = get_or_create_transfer_context(room, sender, payload)

        room_state = get_room_lan_state(room)
        if room_state == 'PAIR_DIFF_LAN':
            instruct_upload_relay(context, 'room_diff_lan')
            logger.info(f"Skipped file_available for room {room} due to PAIR_DIFF_LAN; instructed sender {sender} to relay")
            return

        emit('file_available', payload, room=room, include_self=False)
        logger.info(f"Relayed file_available to room: {room}")
        debug_signal_log('tx', payload, room=room, event='file_available', sender=sender)

        update_transfer_state(context, 'waiting_result', 'lan_offer_sent')
        socketio.start_background_task(transfer_decision_timeout_worker, context.get('transfer_id'))

        filename = payload.get('filename', 'Unknown File')
        file_id = payload.get('file_id', 'Unknown ID')
        emit_activity_log('file_available', room, sender, f"{filename} ({file_id})")

    @socketio.on('file_sync_completed')
    def handle_file_sync_completed(data):
        room, payload = resolve_signal_context(data)
        sender = get_client_from_sid(request.sid)
        debug_signal_log('rx', data, room=room, event='file_sync_completed', sender=sender)
        if room:
            if not ensure_protocol_version(payload, 'file_sync_completed'):
                return

            if not is_sender_authorized_for_room(sender, room):
                logger.warning(f"Rejected file_sync_completed from unauthorized sender={sender} room={room} sid={request.sid}")
                debug_signal_log('drop', payload, room=room, event='file_sync_completed', sender=sender)
                emit('error', {'code': 'E_ROLE_DENIED', 'msg': 'sender is not authorized for this room'}, room=request.sid)
                return

            emit('file_sync_completed', payload, room=room, include_self=False)
            logger.info(f"Relayed file_sync_completed to room: {room}")
            debug_signal_log('tx', payload, room=room, event='file_sync_completed', sender=sender)

            transfer_id = str(payload.get('transfer_id') or '').strip()
            context = TRANSFER_CONTEXTS.get(transfer_id)
            if context and context.get('room') == room:
                instruct_finish(context, reason='lan_ack')

            file_id = payload.get('file_id', 'Unknown ID')
            method = payload.get('method', 'unknown')
            emit_activity_log('file_sync_completed', room, sender, f"{file_id} via {method}")
        else:
            logger.warning(f"Dropped file_sync_completed due to missing room. sid={request.sid}, data={data}")

    @socketio.on('file_need_relay')
    def handle_file_need_relay(data):
        room, payload = resolve_signal_context(data)
        sender = get_client_from_sid(request.sid)
        debug_signal_log('rx', data, room=room, event='file_need_relay', sender=sender)
        if room:
            if not ensure_protocol_version(payload, 'file_need_relay'):
                return

            if not is_sender_authorized_for_room(sender, room):
                logger.warning(f"Rejected file_need_relay from unauthorized sender={sender} room={room} sid={request.sid}")
                debug_signal_log('drop', payload, room=room, event='file_need_relay', sender=sender)
                emit('error', {'code': 'E_ROLE_DENIED', 'msg': 'sender is not authorized for this room'}, room=request.sid)
                return

            emit('file_need_relay', payload, room=room, include_self=False)
            logger.info(f"Relayed file_need_relay to room: {room}")
            debug_signal_log('tx', payload, room=room, event='file_need_relay', sender=sender)

            transfer_id = str(payload.get('transfer_id') or '').strip()
            context = TRANSFER_CONTEXTS.get(transfer_id)
            if context and context.get('room') == room:
                reason = payload.get('reason', 'receiver_requested_fallback')
                instruct_upload_relay(context, reason)

            file_id = payload.get('file_id', 'Unknown ID')
            reason = payload.get('reason', 'unspecified')
            emit_activity_log('file_need_relay', room, sender, f"{file_id}: {reason}")
        else:
            logger.warning(f"Dropped file_need_relay due to missing room. sid={request.sid}, data={data}")


