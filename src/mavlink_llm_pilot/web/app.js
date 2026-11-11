const $ = (id) => document.getElementById(id);
const format = (value, digits = 1) => value === null || value === undefined ? "—" : Number(value).toFixed(digits);
const gpsFixTypes = {
  0: ["NO GPS", "No GPS connected or detected"],
  1: ["NO FIX", "Receiving valid GPS messages but no lock"],
  2: ["2D FIX", "Receiving valid messages and 2D lock"],
  3: ["3D FIX", "Receiving valid messages and 3D lock"],
  4: ["DGPS", "3D lock with differential improvements"],
  5: ["RTK FLOAT", "3D RTK Float"],
  6: ["RTK FIXED", "3D RTK Fixed"],
  7: ["STATIC", "Static fix, typically for base stations"],
  8: ["PPP", "Improved positioning with PPP"],
};

function setText(id, value) { $(id).textContent = value; }

function escapeHtml(value) {
  return value.replace(/[&<>"']/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[character]);
}

function inlineMarkdown(value) {
  let html = escapeHtml(value);
  html = html.replace(/`([^`]+)`/g, "<code>$1</code>");
  html = html.replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
  html = html.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/__([^_]+)__/g, "<strong>$1</strong>");
  html = html.replace(/\*([^*]+)\*/g, "<em>$1</em>");
  html = html.replace(/_([^_]+)_/g, "<em>$1</em>");
  return html;
}

function markdownToHtml(markdown) {
  const lines = markdown.replace(/\r\n/g, "\n").split("\n");
  const output = [];
  let inCode = false;
  let code = [];
  let listType = null;
  const closeList = () => {
    if (listType) output.push(`</${listType}>`);
    listType = null;
  };
  for (const line of lines) {
    if (line.trim().startsWith("```")) {
      if (inCode) {
        output.push(`<pre><code>${escapeHtml(code.join("\n"))}</code></pre>`);
        code = []; inCode = false;
      } else {
        closeList(); inCode = true;
      }
      continue;
    }
    if (inCode) { code.push(line); continue; }
    const heading = line.match(/^(#{1,3})\s+(.+)$/);
    const bullet = line.match(/^\s*[-*]\s+(.+)$/);
    const numbered = line.match(/^\s*\d+\.\s+(.+)$/);
    if (heading) { closeList(); output.push(`<h${heading[1].length}>${inlineMarkdown(heading[2])}</h${heading[1].length}>`); continue; }
    if (bullet || numbered) {
      const desired = numbered ? "ol" : "ul";
      if (listType !== desired) { closeList(); output.push(`<${desired}>`); listType = desired; }
      output.push(`<li>${inlineMarkdown((bullet || numbered)[1])}</li>`);
      continue;
    }
    closeList();
    if (line.trim()) output.push(`<p>${inlineMarkdown(line)}</p>`);
  }
  if (inCode) output.push(`<pre><code>${escapeHtml(code.join("\n"))}</code></pre>`);
  closeList();
  return output.join("");
}

function renderTelemetry(data) {
  setText("connection-label", "MAVLINK ONLINE");
  $("connection-dot").style.background = "var(--green)";
  setText("mode", data.mode || "UNKNOWN");
  setText("armed", data.armed ? "ARMED" : "DISARMED");
  $("armed").style.color = data.armed ? "var(--amber)" : "var(--green)";
  const position = data.position || {};
  const gps = data.gps || {};
  const attitude = data.attitude_degrees || {};
  const battery = data.battery || {};
  setText("altitude", format(position.altitude_m_relative));
  setText("speed", format(data.groundspeed_m_s));
  setText("heading", format(data.heading_degrees, 0));
  setText("battery", battery.remaining_percent == null ? "—" : `${battery.remaining_percent}%`);
  setText("voltage", battery.voltage_v == null ? "—" : `${format(battery.voltage_v, 2)} V`);
  setText("latitude", format(position.latitude_degrees, 6));
  setText("longitude", format(position.longitude_degrees, 6));
  const gpsFix = gpsFixTypes[gps.fix_type];
  setText("gps-fix", gpsFix ? gpsFix[0] : gps.fix_type == null ? "—" : `UNKNOWN (${gps.fix_type})`);
  $("gps-fix").title = gpsFix ? gpsFix[1] : "";
  setText("satellites", gps.satellites_visible == null ? "—" : gps.satellites_visible);
  setText("roll", format(attitude.roll, 1)); setText("pitch", format(attitude.pitch, 1)); setText("yaw", format(attitude.yaw, 1));
  const roll = Number(attitude.roll || 0);
  const pitch = Number(attitude.pitch || 0);
  $("horizon-line").style.transform = `translateY(${pitch * 3}px) rotate(${roll}deg)`;
  setText("vertical-speed", format(data.vertical_speed_m_s));
  const local = data.local_position_ned_m;
  setText("local-position", local ? `${format(local.north)}, ${format(local.east)}, ${format(local.down)}` : "—");
  const ages = data.message_age_seconds || {};
  const age = ages.HEARTBEAT ?? Math.max(...Object.values(ages), 0);
  setText("link-age", `${format(age, 1)}s`);
  setText("last-update", `UPDATED ${new Date().toLocaleTimeString()}`);
}

async function pollTelemetry() {
  try {
    const response = await fetch("/api/telemetry", { cache: "no-store" });
    if (!response.ok) throw new Error(await response.text());
    renderTelemetry(await response.json());
  } catch (error) {
    setText("connection-label", "MAVLINK OFFLINE");
    $("connection-dot").style.background = "var(--red)";
    setText("last-update", "Telemetry unavailable");
  }
}

function addMessage(role, text) {
  const node = document.createElement("div");
  node.className = `message ${role}`;
  const tag = document.createElement("span");
  tag.className = "message-tag";
  tag.textContent = role === "user" ? "YOU" : "PILOT AI";
  const content = document.createElement("div");
  content.className = "message-content";
  content.innerHTML = role === "assistant" ? markdownToHtml(text) : `<p>${escapeHtml(text)}</p>`;
  node.append(tag, content);
  $("chat-log").appendChild(node);
  $("chat-log").scrollTop = $("chat-log").scrollHeight;
}

async function sendMessage() {
  const input = $("message");
  const message = input.value.trim();
  if (!message) return;
  const confirmLive = $("confirm-live").checked;
  addMessage("user", message);
  input.value = "";
  $("send").disabled = true;
  try {
    const response = await fetch("/api/chat", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, confirm_live: confirmLive }),
    });
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail || "Request failed");
    await watchChatJob(body.job_id);
  } catch (error) {
    addMessage("assistant", `ERROR: ${error.message}`);
  } finally { $("send").disabled = false; }
}

async function watchChatJob(jobId) {
  let actionVersion = 0;
  while (true) {
    const response = await fetch(`/api/chat/${jobId}`, { cache: "no-store" });
    const job = await readResponse(response);
    if (!response.ok) throw new Error(job.detail || job.error || "Chat request failed");
    if (job.action_version > actionVersion) {
      actionVersion = job.action_version;
      addToolMessage(jobId, job.action, job.arguments, job.status === "approval_required");
    }
    if (job.status === "complete") {
      addMessage("assistant", job.response || "");
      return;
    }
    if (job.status === "error") throw new Error(job.error || "Chat request failed");
    await new Promise((resolve) => setTimeout(resolve, 350));
  }
}

function addToolMessage(jobId, action, arguments_, needsApproval) {
  const node = document.createElement("div");
  node.className = "message tool";
  node.innerHTML = `<span class="message-tag">FLIGHT TOOL // ${needsApproval ? "AWAITING APPROVAL" : "EXECUTING"}</span><div class="message-content"><p><strong></strong><br><code></code></p></div>`;
  node.querySelector("strong").textContent = `${action}()`;
  node.querySelector("code").textContent = JSON.stringify(arguments_, null, 2);
  if (needsApproval) {
    const controls = document.createElement("div");
    controls.className = "approval-controls";
    controls.innerHTML = '<button class="approve">APPROVE</button><button class="deny">DENY</button>';
    controls.querySelector(".approve").onclick = () => resolveApproval(jobId, true, controls);
    controls.querySelector(".deny").onclick = () => resolveApproval(jobId, false, controls);
    node.appendChild(controls);
  }
  $("chat-log").appendChild(node);
  $("chat-log").scrollTop = $("chat-log").scrollHeight;
}

async function resolveApproval(jobId, approved, controls) {
  controls.querySelectorAll("button").forEach((button) => { button.disabled = true; });
  controls.querySelector(approved ? ".approve" : ".deny").textContent = approved ? "APPROVED" : "DENIED";
  const response = await fetch(`/api/chat/${jobId}/approval?approved=${approved}`, { method: "POST" });
  if (!response.ok) {
    const body = await readResponse(response);
    addMessage("assistant", `ERROR: ${body.detail || body.error || "Could not submit approval."}`);
  }
}

async function readResponse(response) {
  const contentType = response.headers.get("content-type") || "";
  if (contentType.includes("application/json")) return response.json();
  const text = await response.text();
  return { error: text || `Request failed (${response.status})` };
}

function setupVoice() {
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) {
    $("voice").disabled = true;
    $("voice-status").textContent = "Speech recognition is not supported by this browser. Type commands instead.";
    return;
  }
  const recognition = new SpeechRecognition();
  recognition.lang = "en-US"; recognition.interimResults = true; recognition.continuous = false;
  let spokenText = "";
  recognition.onstart = () => { $("voice").classList.add("recording"); $("voice-status").textContent = "Listening..."; };
  recognition.onend = () => {
    $("voice").classList.remove("recording");
    $("voice-status").textContent = "Voice input ready.";
    if (spokenText.trim()) {
      $("message").value = spokenText.trim();
      spokenText = "";
      sendMessage();
    }
  };
  recognition.onerror = (event) => { $("voice-status").textContent = `Voice input error: ${event.error}`; };
  recognition.onresult = (event) => {
    spokenText = Array.from(event.results).map((result) => result[0].transcript).join("");
    $("message").value = spokenText;
  };
  $("voice").addEventListener("click", () => recognition.start());
}

$("send").addEventListener("click", sendMessage);
$("message").addEventListener("keydown", (event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); sendMessage(); } });
setupVoice(); pollTelemetry(); setInterval(pollTelemetry, 1500);
