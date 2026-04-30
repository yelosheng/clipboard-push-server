document.addEventListener("DOMContentLoaded", () => {
    const elements = {
        roomList: document.getElementById("room-list"),
        clientList: document.getElementById("client-list"),
        totalConnections: document.getElementById("total-connections"),
        totalSessions: document.getElementById("total-sessions"),
        uptime: document.getElementById("uptime"),
        activityLog: document.getElementById("activity-log"),
        activityCount: document.getElementById("activity-count"),
        activityFilter: document.getElementById("activity-filter"),
        activityFilterAll: document.getElementById("activity-filter-all"),
        activityFilterNone: document.getElementById("activity-filter-none"),
        clearActivity: document.getElementById("clear-activity"),
        statusDot: document.getElementById("connection-status"),
        connectionText: document.getElementById("connection-text"),
        roomTitle: document.getElementById("current-room-title"),
        r2SectionTitle: document.getElementById("r2-section-title"),
        r2BucketLabel: document.getElementById("r2-bucket-label"),
        r2BucketName: document.getElementById("r2-bucket-name"),
        r2ObjectsCount: document.getElementById("r2-objects-count"),
        r2TotalSize: document.getElementById("r2-total-size"),
        r2TotalBytes: document.getElementById("r2-total-bytes"),
        r2ScannedObjects: document.getElementById("r2-scanned-objects"),
        r2UpdatedAt: document.getElementById("r2-updated-at"),
        r2StatusText: document.getElementById("r2-status-text"),
        r2RefreshBtn: document.getElementById("r2-refresh-btn"),
        r2EmptyBtn: document.getElementById("r2-empty-btn")
    };

    if (!elements.roomList || !elements.clientList) {
        return;
    }

    let currentClients = {};
    let roomStates = {};
    let selectedRoom = "all";
    let uptimeSeconds = 0;

    const socket = typeof io === "function" ? io() : null;

    const typeGlyphMap = {
        pc: "PC",
        windows: "WS",
        macos: "MC",
        linux: "LX",
        android: "AN",
        app: "AP",
        ios: "IO",
        web: "WB",
        cli: "CL",
        unknown: "--"
    };

    const roomStateLabelMap = {
        EMPTY: "Empty",
        SINGLE: "Single",
        PAIR_UNKNOWN: "Pair Unknown",
        PAIR_SAME_LAN: "Same LAN",
        PAIR_DIFF_LAN: "Diff LAN",
        UNKNOWN: "Unknown"
    };

    const activityTypeOrder = [
        "connect",
        "disconnect",
        "join",
        "leave",
        "heartbeat",
        "sys",
        "err",
        "sync",
        "clipboard",
        "file",
        "file_announcement",
        "file_ack",
        "file_request_relay",
        "file_available",
        "file_sync_completed",
        "file_need_relay",
        "room_state_changed",
        "peer_evicted",
        "lan_probe_request",
        "lan_probe_result",
        "peer_network_update",
        "transfer_command",
        "transfer_state",
        "api_relay"
    ];

    const activityTypeLabels = {
        connect: "🟢 Connect",
        disconnect: "🔴 Disconnect",
        join: "📥 Join",
        leave: "📤 Leave",
        heartbeat: "💓 Heartbeat",
        sys: "System",
        err: "Error",
        sync: "Sync",
        clipboard: "Clipboard",
        file: "File",
        file_announcement: "LAN Announce",
        file_ack: "LAN Ack",
        file_request_relay: "LAN Fallback",
        file_available: "File Available",
        file_sync_completed: "Sync Completed",
        file_need_relay: "Need Relay",
        room_state_changed: "Room State",
        peer_evicted: "Peer Evicted",
        lan_probe_request: "Probe Request",
        lan_probe_result: "Probe Result",
        peer_network_update: "Network Update",
        transfer_command: "Transfer Cmd",
        transfer_state: "Transfer State",
        api_relay: "API Relay"
    };

    const knownActivityTypes = new Set(activityTypeOrder);
    const enabledActivityTypes = new Set(activityTypeOrder);

    // --- Client color palette (12 distinct colors for easy identification) ---
    const clientColorPalette = [
        "#22d3ee", "#f472b6", "#a78bfa", "#34d399",
        "#fb923c", "#60a5fa", "#facc15", "#f87171",
        "#2dd4bf", "#c084fc", "#4ade80", "#fbbf24"
    ];
    const clientColorMap = {};
    let clientColorIndex = 0;

    function getClientColor(clientId) {
        if (!clientId || clientId === "Unknown" || clientId === "server") {
            return "#9bb1cc";
        }
        if (!clientColorMap[clientId]) {
            clientColorMap[clientId] = clientColorPalette[clientColorIndex % clientColorPalette.length];
            clientColorIndex++;
        }
        return clientColorMap[clientId];
    }

    // --- Client filter state ---
    let selectedClientFilter = "all";
    const clientFilterEl = document.getElementById("client-filter");

    elements.clearActivity?.addEventListener("click", () => {
        elements.activityLog.innerHTML = "";
        updateActivityCount();
    });

    elements.activityFilterAll?.addEventListener("click", () => {
        knownActivityTypes.forEach((type) => enabledActivityTypes.add(type));
        renderActivityFilters();
        applyActivityFilters();
    });

    elements.activityFilterNone?.addEventListener("click", () => {
        enabledActivityTypes.clear();
        renderActivityFilters();
        applyActivityFilters();
    });

    if (clientFilterEl) {
        clientFilterEl.addEventListener("change", () => {
            selectedClientFilter = clientFilterEl.value;
            applyActivityFilters();
        });
    }

    elements.activityFilter?.addEventListener("click", (event) => {
        const button = event.target.closest("button[data-activity-type]");
        if (!button) {
            return;
        }

        const activityType = String(button.dataset.activityType || "").toLowerCase();
        if (!activityType) {
            return;
        }

        if (enabledActivityTypes.has(activityType)) {
            enabledActivityTypes.delete(activityType);
        } else {
            enabledActivityTypes.add(activityType);
        }

        renderActivityFilters();
        applyActivityFilters();
    });

    elements.r2RefreshBtn?.addEventListener("click", () => {
        loadR2Usage(false);
    });

    elements.r2EmptyBtn?.addEventListener("click", async () => {
        const isLocal = elements.r2SectionTitle?.textContent?.includes("Local");
        const confirmMsg = isLocal
            ? "This will permanently delete all files in local storage. Continue?"
            : "This will permanently delete all objects in the R2 bucket. Continue?";
        const ok = window.confirm(confirmMsg);
        if (!ok) {
            return;
        }

        setR2Status(isLocal ? "Clearing local storage..." : "Emptying bucket...", true);
        setR2ButtonsDisabled(true);

        try {
            const response = await fetch("/api/dashboard/r2_empty", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json"
                }
            });
            const payload = await response.json();
            if (!response.ok) {
                throw new Error(payload?.error || "Failed to empty storage.");
            }

            fillR2Usage(payload?.usage || {}, payload?.updated_at_epoch_ms);
            setR2Status(isLocal ? "Local storage cleared." : "Bucket emptied.", false);
            log("sys", "Storage", isLocal ? "Local storage cleared from dashboard." : "R2 bucket emptied from dashboard.", "dashboard_room");
        } catch (error) {
            const message = error instanceof Error ? error.message : "Unknown error";
            setR2Status(`Empty failed: ${message}`, false, true);
            log("err", "Storage", `Empty failed: ${message}`, "dashboard_room");
        } finally {
            setR2ButtonsDisabled(false);
        }
    });

    setInterval(() => {
        uptimeSeconds += 1;
        const h = Math.floor(uptimeSeconds / 3600).toString().padStart(2, "0");
        const m = Math.floor((uptimeSeconds % 3600) / 60).toString().padStart(2, "0");
        const s = (uptimeSeconds % 60).toString().padStart(2, "0");
        elements.uptime.textContent = `${h}:${m}:${s}`;
    }, 1000);

    elements.roomList.addEventListener("click", (event) => {
        const button = event.target.closest("button[data-room]");
        if (!button) {
            return;
        }

        selectedRoom = button.dataset.room || "all";
        render();
    });

    renderActivityFilters();
    loadR2Usage(true);

    if (socket) {
        socket.on("connect", () => {
            updateConnectionState(true);
            log("sys", "System", "Connected to relay server.", "dashboard_room");
            socket.emit("join", { room: "dashboard_room" });
        });

        socket.on("disconnect", () => {
            updateConnectionState(false);
            log("err", "System", "Connection lost.", "dashboard_room");
        });

        socket.on("client_list_update", (clients) => {
            currentClients = clients || {};
            render();
            log("sync", "System", "Client list synchronized.", "dashboard_room");
        });

        socket.on("room_states_snapshot", (payload) => {
            roomStates = payload?.rooms || {};
            render();
        });

        socket.on("room_state_changed", (state) => {
            if (!state || !state.room) {
                return;
            }
            roomStates[state.room] = state;
            render();
        });

        socket.on("activity_log", (data) => {
            const type = String(data?.type || "info");
            const sender = String(data?.sender || "Unknown");
            const content = String(data?.content || "");
            const room = String(data?.room || "Unknown");
            const clientId = String(data?.client_id || "");
            log(type, sender, content, room, clientId);
        });
    } else {
        log("err", "System", "Socket client unavailable.", "dashboard_room");
    }

    function updateConnectionState(isOnline) {
        elements.statusDot.classList.toggle("online", isOnline);
        elements.connectionText.textContent = isOnline ? "Online" : "Offline";
    }

    function setR2ButtonsDisabled(disabled) {
        if (elements.r2RefreshBtn) {
            elements.r2RefreshBtn.disabled = disabled;
        }
        if (elements.r2EmptyBtn) {
            elements.r2EmptyBtn.disabled = disabled;
        }
    }

    function setR2Status(message, loading = false, isError = false) {
        if (!elements.r2StatusText) {
            return;
        }
        elements.r2StatusText.textContent = message;
        elements.r2StatusText.classList.toggle("status-text-active", loading && !isError);
        elements.r2StatusText.classList.toggle("status-text-error", isError);
    }

    function numberFmt(value) {
        const num = Number(value);
        if (!Number.isFinite(num)) {
            return "-";
        }
        return num.toLocaleString("en-US");
    }

    function fillR2Usage(usage, updatedAtEpochMs) {
        const isLocal = usage?.backend === "local";
        if (elements.r2SectionTitle) {
            elements.r2SectionTitle.textContent = isLocal ? "Local Storage" : "R2 Bucket Usage";
        }
        if (elements.r2BucketLabel) {
            elements.r2BucketLabel.textContent = isLocal ? "Path" : "Bucket";
        }
        if (elements.r2BucketName) {
            elements.r2BucketName.textContent = String(usage?.bucket || "-");
        }
        if (elements.r2ObjectsCount) {
            elements.r2ObjectsCount.textContent = numberFmt(usage?.objects_count);
        }
        if (elements.r2TotalSize) {
            elements.r2TotalSize.textContent = isLocal ? "-" : String(usage?.total_human || "-");
        }
        if (elements.r2TotalBytes) {
            elements.r2TotalBytes.textContent = isLocal ? "-" : numberFmt(usage?.total_bytes);
        }
        if (elements.r2ScannedObjects) {
            elements.r2ScannedObjects.textContent = isLocal ? "-" : numberFmt(usage?.scanned_objects);
        }

        const updatedAt = updatedAtEpochMs != null ? new Date(updatedAtEpochMs) : new Date();
        if (elements.r2UpdatedAt) {
            elements.r2UpdatedAt.textContent = updatedAt.toLocaleString("en-GB");
        }
    }

    async function loadR2Usage(initial = false) {
        if (!elements.r2StatusText) {
            return;
        }

        setR2Status(initial ? "Loading bucket usage..." : "Refreshing bucket usage...", true);
        setR2ButtonsDisabled(true);

        try {
            const response = await fetch("/api/dashboard/r2_usage", {
                method: "GET",
                headers: {
                    Accept: "application/json"
                }
            });
            const usage = await response.json();
            if (!response.ok) {
                throw new Error(usage?.error || "Failed to fetch bucket usage.");
            }

            fillR2Usage(usage, usage?.updated_at_epoch_ms);
            setR2Status("Bucket usage updated.", false);
        } catch (error) {
            const message = error instanceof Error ? error.message : "Unknown error";
            setR2Status(`Update failed: ${message}`, false, true);
            log("err", "R2", `Usage fetch failed: ${message}`, "dashboard_room");
        } finally {
            setR2ButtonsDisabled(false);
        }
    }

    function render() {
        const rooms = {};
        let totalClientCount = 0;
        let totalSessionCount = 0;

        Object.entries(currentClients).forEach(([clientId, rawClient]) => {
            const normalized = normalizeClient(rawClient);
            if (!rooms[normalized.room]) {
                rooms[normalized.room] = [];
            }

            rooms[normalized.room].push({
                id: clientId,
                room: normalized.room,
                type: normalized.type,
                sessions: normalized.sessions,
                network: normalized.network,
                roomState: normalized.roomState,
                sameLan: normalized.sameLan,
                lanConfidence: normalized.lanConfidence,
                deviceName: normalized.deviceName
            });

            totalClientCount += 1;
            totalSessionCount += normalized.sessions.length;
        });

        elements.totalConnections.textContent = String(totalClientCount);
        elements.totalSessions.textContent = String(totalSessionCount);

        renderSidebar(rooms, totalClientCount);
        renderTable(rooms);
        updateClientFilterOptions();
    }

    function normalizeClient(client) {
        if (!client) {
            return {
                room: "Unknown",
                type: "unknown",
                sessions: [],
                network: {},
                roomState: "UNKNOWN",
                sameLan: false,
                lanConfidence: "none",
                deviceName: "Unknown"
            };
        }

        if (Array.isArray(client)) {
            return {
                room: "Unknown",
                type: "unknown",
                sessions: client,
                network: {},
                roomState: "UNKNOWN",
                sameLan: false,
                lanConfidence: "none",
                deviceName: "Unknown"
            };
        }

        const room = String(client.room || "Unknown");
        const sessions = Array.isArray(client.sids) ? client.sids : [];
        const type = String(client.type || client.client_type || client.clientType || "unknown").toLowerCase();
        const network = client.network && typeof client.network === "object" ? client.network : {};

        const stateFromRoom = String(roomStates?.[room]?.state || "").toUpperCase();
        const roomState = stateFromRoom || String(client.room_state || "UNKNOWN").toUpperCase();

        const sameLanValue = roomStates?.[room]?.same_lan;
        const sameLan = typeof sameLanValue === "boolean" ? sameLanValue : Boolean(client.same_lan);

        const confidenceFromRoom = String(roomStates?.[room]?.lan_confidence || "");
        const lanConfidence = confidenceFromRoom || String(client.lan_confidence || "none");
        const deviceName = String(client.device_name || client.deviceName || "").trim() || client.id || "Unknown";

        return { room, type, sessions, network, roomState, sameLan, lanConfidence, deviceName };
    }

    function renderSidebar(rooms, total) {
        const allRooms = Object.keys(rooms).sort((a, b) => a.localeCompare(b));
        const rows = [
            roomButtonHtml("all", "All Rooms", total, selectedRoom === "all", "")
        ];

        allRooms.forEach((roomName) => {
            const stateCode = String(roomStates?.[roomName]?.state || "").toUpperCase();
            const stateLabel = roomStateLabelMap[stateCode] || stateCode;
            rows.push(roomButtonHtml(roomName, roomName, rooms[roomName].length, selectedRoom === roomName, stateLabel));
        });

        elements.roomList.innerHTML = rows.join("");
    }

    function roomButtonHtml(room, label, count, isActive, stateLabel) {
        const activeClass = isActive ? " active" : "";
        const safeRoom = escapeHtml(room);
        const safeLabel = escapeHtml(label);
        const safeState = stateLabel ? `<span class="room-state-mini">${escapeHtml(stateLabel)}</span>` : "";

        return `<li><button type="button" class="room-link${activeClass}" data-room="${safeRoom}"><span>${safeLabel}${safeState}</span><span class="nav-badge">${count}</span></button></li>`;
    }

    function renderTable(rooms) {
        let rows = [];

        if (selectedRoom === "all") {
            rows = Object.values(rooms).flat();
            elements.roomTitle.textContent = "All Rooms Overview";
        } else {
            rows = rooms[selectedRoom] || [];
            const selectedState = String(roomStates?.[selectedRoom]?.state || "").toUpperCase();
            const selectedStateLabel = roomStateLabelMap[selectedState] || "Unknown";
            elements.roomTitle.textContent = `Room: ${selectedRoom} | ${selectedStateLabel}`;
        }

        elements.clientList.innerHTML = "";

        if (rows.length === 0) {
            const tr = document.createElement("tr");
            const td = document.createElement("td");
            td.colSpan = 6;
            td.className = "empty-cell";
            td.textContent = "No active clients in this view.";
            tr.appendChild(td);
            elements.clientList.appendChild(tr);
            return;
        }

        rows.forEach((client) => {
            const tr = document.createElement("tr");

            const color = getClientColor(client.id);
            const idDisplay = client.deviceName || client.id;
            const idTd = document.createElement("td");
            const dot = document.createElement("span");
            dot.className = "client-color-dot";
            dot.style.backgroundColor = color;
            idTd.appendChild(dot);
            idTd.appendChild(document.createTextNode(" " + idDisplay));
            tr.appendChild(idTd);

            tr.appendChild(typeCell(client.type));
            tr.appendChild(cell(client.room));
            tr.appendChild(lanStateCell(client));
            tr.appendChild(networkCell(client.network));
            tr.appendChild(cell(String(client.sessions.length)));

            elements.clientList.appendChild(tr);
        });
    }

    function cell(value) {
        const td = document.createElement("td");
        td.textContent = value;
        return td;
    }

    function typeCell(type) {
        const normalizedType = String(type || "unknown").toLowerCase();
        const glyph = typeGlyphMap[normalizedType] || typeGlyphMap.unknown;

        const td = document.createElement("td");
        const chip = document.createElement("span");
        chip.className = "type-chip";

        const glyphEl = document.createElement("span");
        glyphEl.className = "type-glyph";
        glyphEl.setAttribute("aria-hidden", "true");
        glyphEl.textContent = glyph;

        const labelEl = document.createElement("span");
        labelEl.textContent = normalizedType.toUpperCase();

        chip.appendChild(glyphEl);
        chip.appendChild(labelEl);
        td.appendChild(chip);

        return td;
    }

    function lanStateCell(client) {
        const td = document.createElement("td");
        const stateCode = String(client.roomState || "UNKNOWN").toUpperCase();
        const label = roomStateLabelMap[stateCode] || stateCode;
        const confidence = String(client.lanConfidence || "none").toLowerCase();

        const chip = document.createElement("span");
        chip.className = `lan-state-chip state-${stateCode.toLowerCase()}`;
        chip.textContent = label;

        const meta = document.createElement("span");
        meta.className = "lan-state-meta";
        meta.textContent = `${client.sameLan ? "LAN:YES" : "LAN:NO"} | ${confidence}`;

        td.appendChild(chip);
        td.appendChild(meta);
        return td;
    }

    function networkCell(network) {
        const td = document.createElement("td");
        const privateIp = String(network?.private_ip || "-");
        const cidr = String(network?.cidr || "-");
        const epoch = network?.network_epoch != null ? String(network.network_epoch) : "-";

        const line1 = document.createElement("div");
        line1.className = "network-line";
        line1.textContent = privateIp;

        const line2 = document.createElement("div");
        line2.className = "network-meta";
        line2.textContent = `${cidr} | epoch:${epoch}`;

        td.appendChild(line1);
        td.appendChild(line2);
        return td;
    }

    function normalizeActivityType(type) {
        return String(type || "info").toLowerCase();
    }

    function ensureActivityTypeRegistered(activityType) {
        if (!knownActivityTypes.has(activityType)) {
            knownActivityTypes.add(activityType);
            enabledActivityTypes.add(activityType);
            renderActivityFilters();
        }
    }

    function activityTypeButtonHtml(activityType, isEnabled) {
        const label = activityTypeLabels[activityType] || activityType.toUpperCase();
        const activeClass = isEnabled ? " active" : "";
        return `<button type="button" class="activity-filter-chip${activeClass}" data-activity-type="${escapeHtml(activityType)}" aria-pressed="${isEnabled ? "true" : "false"}">${escapeHtml(label)}</button>`;
    }

    function renderActivityFilters() {
        if (!elements.activityFilter) {
            return;
        }

        const ordered = Array.from(knownActivityTypes).sort((a, b) => {
            const ai = activityTypeOrder.indexOf(a);
            const bi = activityTypeOrder.indexOf(b);
            const av = ai === -1 ? Number.MAX_SAFE_INTEGER : ai;
            const bv = bi === -1 ? Number.MAX_SAFE_INTEGER : bi;
            if (av === bv) {
                return a.localeCompare(b);
            }
            return av - bv;
        });

        elements.activityFilter.innerHTML = ordered
            .map((type) => activityTypeButtonHtml(type, enabledActivityTypes.has(type)))
            .join("");
    }

    function applyActivityFilters() {
        if (!elements.activityLog) {
            return;
        }

        Array.from(elements.activityLog.children).forEach((entry) => {
            const activityType = String(entry.dataset.activityType || "info").toLowerCase();
            const entryClientId = String(entry.dataset.clientId || "");

            let visible = enabledActivityTypes.has(activityType);
            if (visible && selectedClientFilter !== "all" && entryClientId) {
                visible = entryClientId === selectedClientFilter;
            }
            entry.classList.toggle("is-hidden", !visible);
        });

        updateActivityCount();
    }

    function log(type, sender, content, room, clientId) {
        const item = document.createElement("div");
        const normalizedType = normalizeActivityType(type);
        ensureActivityTypeRegistered(normalizedType);

        item.className = `log-item level-${normalizedType}`;
        item.dataset.activityType = normalizedType;
        if (clientId) {
            item.dataset.clientId = clientId;
        }

        const time = document.createElement("span");
        time.className = "log-time";
        time.textContent = new Date().toLocaleTimeString("en-GB");

        const tag = document.createElement("span");
        tag.className = "log-tag";
        tag.textContent = `[${activityTypeLabels[normalizedType] || normalizedType.toUpperCase()}]`;

        const senderEl = document.createElement("span");
        senderEl.className = "log-sender";
        const senderColor = clientId ? getClientColor(clientId) : stringToColor(sender);
        senderEl.style.color = senderColor;
        if (clientId) {
            const dot = document.createElement("span");
            dot.className = "log-client-dot";
            dot.style.backgroundColor = senderColor;
            senderEl.appendChild(dot);
        }
        senderEl.appendChild(document.createTextNode(`${sender} @ ${room && room !== "Unknown" ? room : "\u2014"}`));

        const contentEl = document.createElement("span");
        contentEl.className = "log-content";
        contentEl.textContent = content;

        item.appendChild(time);
        item.appendChild(tag);
        item.appendChild(senderEl);
        item.appendChild(contentEl);

        elements.activityLog.prepend(item);

        if (elements.activityLog.children.length > 200) {
            elements.activityLog.removeChild(elements.activityLog.lastChild);
        }

        applyActivityFilters();
    }

    function updateActivityCount() {
        if (elements.activityCount && elements.activityLog) {
            const total = elements.activityLog.children.length;
            const visible = elements.activityLog.querySelectorAll(".log-item:not(.is-hidden)").length;
            elements.activityCount.textContent = `${visible}/${total}`;
        }
    }

    function stringToColor(value) {
        if (!value) {
            return "#9bb1cc";
        }

        let hash = 0;
        for (let i = 0; i < value.length; i += 1) {
            hash = value.charCodeAt(i) + ((hash << 5) - hash);
        }

        const hue = Math.abs(hash) % 360;
        return `hsl(${hue}, 72%, 70%)`;
    }

    function escapeHtml(input) {
        return String(input)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/\"/g, "&quot;")
            .replace(/'/g, "&#039;");
    }

    function updateClientFilterOptions() {
        if (!clientFilterEl) {
            return;
        }
        const previousValue = clientFilterEl.value;
        const options = ['<option value="all">All Clients</option>'];

        const sortedIds = Object.keys(currentClients).sort();
        sortedIds.forEach((clientId) => {
            const client = currentClients[clientId];
            const deviceName = String(client?.device_name || client?.deviceName || "").trim() || clientId;
            const color = getClientColor(clientId);
            const label = `\u25CF ${deviceName}`;
            const selected = clientId === previousValue ? " selected" : "";
            options.push(`<option value="${escapeHtml(clientId)}" style="color:${color}"${selected}>${escapeHtml(label)}</option>`);
        });

        clientFilterEl.innerHTML = options.join("");
        // Restore selection if still valid
        if (previousValue && sortedIds.includes(previousValue)) {
            clientFilterEl.value = previousValue;
        } else {
            clientFilterEl.value = "all";
            selectedClientFilter = "all";
        }
    }
});



