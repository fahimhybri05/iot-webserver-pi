// GPIO numbers are Pi BCM pins for this board's header (re-derived from the
// schematic - see the port's plan file / memory/rpi4_esp32p4_gpio_reference.md).
// Cosmetic only, used for the on-screen "DO0 · GPIO 22" / "DI 3 · GPIO 26" labels.
const DO_PINS = [22, 27];
const DI_PINS = [16, 19, 20, 26, 21, 25, 5, 12, 6, 13];
let doLabels = ['Relay 0','Relay 1'];
let diLabels = ['Input 1','Input 2','Input 3','Input 4','Input 5','Input 6','Input 7','Input 8','Input 9','Input 10'];
let diModes  = new Array(10).fill('Normal');
let doStates = [false, false];
let diStates = new Array(10).fill(false);
let diCounts = new Array(10).fill(0);
let channelCfg = null;
let rtuSlaves = []; // dynamic RS485 slave list: {slave_id,label,poll_interval_ms,registers:[{label,reg_type,start_addr,unit,data_type,scale}]}
const RTU_MAX_SLAVES = 20;
const RTU_MAX_REGS = 16; // per slave
const DI_MODE_NAMES = ['Normal','Counter','Frequency'];
const DO_MODE_NAMES = ['Normal','Pulse','Timer'];
let ops = 0, startTime = Date.now(), modalCh = 0;

function log(msg, cls=''){
  const lb = document.getElementById('log');
  const ts = new Date().toTimeString().slice(0,8);
  lb.innerHTML += `<div class="log-entry"><span class="log-ts">${ts}</span><span class="log-msg ${cls}">${msg}</span></div>`;
  lb.scrollTop = lb.scrollHeight;
}
function clearLog(){ document.getElementById('log').innerHTML=''; }

/* ── relay render ── */
function renderRelays(){
  document.getElementById('relay-grid').innerHTML = doStates.map((on,i)=>`
    <div class="relay-card${on?' energized':''}" id="rc${i}">
      <div class="rc-id">DO${i} · GPIO ${DO_PINS[i]}</div>
      <input class="rc-label-edit" value="${doLabels[i]}" onchange="doLabels[${i}]=this.value;syncCfg()" title="Click to rename">
      <div class="rc-state-row">
        <div class="rc-led"></div>
        <div class="rc-state">${on?'ENERGIZED':'DE-ENERGIZED'}</div>
        <div class="rc-contact">${on?'CLOSED':'OPEN'}</div>
      </div>
      <div class="rc-btns">
        <div class="btn btn-on"  onclick="setDO(${i},true)">ON</div>
        <div class="btn btn-off" onclick="setDO(${i},false)">OFF</div>
        <div class="btn"         onclick="openPulse(${i})">PULSE</div>
      </div>
      <div class="rc-meta">
        <div class="rc-meta-item">Form A · <span>NO contact</span></div>
        <div class="rc-meta-item">Rating · <span>30 VDC / 1 A</span></div>
      </div>
    </div>`).join('');
}

async function setDO(index, state){
  doStates[index] = state; ops++;
  try {
    const stateParam = state ? "1" : "0";
    const response = await fetch(`/api/relay?id=${index}&state=${stateParam}`);
    if (response.ok) {
      log(`Relay ${index} set to ${state ? 'ON' : 'OFF'}`, 'ok');
    }
  } catch (error) {
    log(`Failed to set Relay ${index}`, 'err');
    console.error("Relay Error:", error);
  }
  renderRelays(); updateSummary();
}
function allRelay(cmd){ doStates.fill(cmd==='on'); renderRelays(); updateSummary(); ops+=2; log(`All relays → ${cmd.toUpperCase()}`, cmd==='on'?'ok':'warn'); }

/* ── DI render ── */
function renderDI(){
  document.getElementById('di-rows').innerHTML = diStates.map((high,i)=>`
    <div class="di-row${high?' high':''}" id="dir${i}">
      <div class="di-ch">DI ${i} · GPIO ${DI_PINS[i]}</div>
      <div><input class="di-label-in" value="${diLabels[i]}" onclick="event.stopPropagation()" onchange="diLabels[${i}]=this.value;syncCfg()"></div>
      <div class="di-led-wrap">
        <div class="di-led"></div>
      </div>
      <div class="di-val">${high?'HIGH':'LOW'}</div>
      <div class="di-mode">${diModes[i]}</div>
      <div style="display:flex;align-items:center;gap:4px;padding-right:4px">
        <span class="di-counter">${diModes[i]==='Counter'?diCounts[i]:'-'}</span>
        ${diModes[i]==='Counter'?`<div class="di-reset-btn" onclick="event.stopPropagation();resetCounter(${i})">RST</div>`:''}
        <div class="di-sim-btn" onclick="event.stopPropagation();toggleDI(${i})">SIM</div>
      </div>
    </div>`).join('');
}

function toggleDI(i){
  diStates[i]=!diStates[i];
  if(diModes[i]==='Counter' && diStates[i]) diCounts[i]++;
  renderDI(); updateSummary();
  log(`DI${i} (${diLabels[i]}) → ${diStates[i]?'HIGH':'LOW'}`, diStates[i]?'ok':'');
}

/* ── summary ── */
function updateSummary(){
  const dc = diStates.filter(Boolean).length;
  const rc = doStates.filter(Boolean).length;
  const d=document.getElementById('di-count'); if(d) d.textContent=dc+' / 10';
  const r=document.getElementById('relay-count'); if(r) r.textContent=rc+' / 2';
  const o=document.getElementById('ops-count'); if(o) o.textContent=ops;
}

let _footerSet = false;
let _boardInfoSet = false;
function updateNetworkInfo(data){
  const ip = data.ip || '—';
  const wip = data.wifi_ip || '—';
  const wcon = data.wifi_connected || false;

  const ipBadge = document.getElementById('ip-badge');
  if(ipBadge) ipBadge.textContent = ip;

  const ethEl = document.getElementById('network-ip');
  if(ethEl) ethEl.textContent = ip;

  const wifiEl = document.getElementById('wifi-ip-display');
  if(wifiEl) wifiEl.textContent = wip !== '' ? wip : '—';

  const wstatEl = document.getElementById('wifi-status');
  if(wstatEl){
    wstatEl.textContent = wcon ? 'CONNECTED' : 'DISCONNECTED';
    wstatEl.className = 'info-val ' + (wcon ? 'green' : 'amber');
  }

  if(!_footerSet && data.version){
    const fv = document.getElementById('footer-ver');
    if(fv) fv.textContent = 'v' + data.version;
    const fc = document.getElementById('footer-copy');
    if(fc) fc.textContent = data.copyright || '';
    _footerSet = true;
  }

  if(!_boardInfoSet && data.board_model){
    const bm = document.getElementById('board-model');
    if(bm) bm.textContent = data.board_model;
    const bc = document.getElementById('board-cpu');
    if(bc) bc.textContent = data.board_cpu || '—';
    _boardInfoSet = true;
  }
}

/* ── config table ── */
function syncCfg(){
  const tb = document.getElementById('cfg-tbody');
  if(!tb) return;
  const mt = 'font-family:var(--font-mono);font-size:9px;color:var(--text3)';
  const doRows = Array.from({length:2}, (_,i) => {
    const lbl  = channelCfg ? channelCfg.do[i].label  : doLabels[i];
    const mode = channelCfg ? DO_MODE_NAMES[channelCfg.do[i].mode] || 'Normal' : 'Normal';
    const inv  = channelCfg ? channelCfg.do[i].invert  : false;
    return `<tr>
      <td><span style="${mt}">DO${i}</span></td>
      <td><span class="tag tag-relay">Relay</span></td>
      <td><input name="do_lbl_${i}" class="cfg-input" value="${lbl}"></td>
      <td><select name="do_mode_${i}" class="cfg-select">
        <option${mode==='Normal'?' selected':''}>Normal</option>
        <option${mode==='Pulse'?' selected':''}>Pulse</option>
        <option${mode==='Timer'?' selected':''}>Timer</option>
      </select></td>
      <td style="text-align:center"><input type="checkbox" name="do_inv_${i}"${inv?' checked':''}></td>
      <td><span style="${mt}">GPIO ${DO_PINS[i]}</span></td>
    </tr>`;
  }).join('');
  const diRows = Array.from({length:10}, (_,i) => {
    const lbl  = channelCfg ? channelCfg.di[i].label  : diLabels[i];
    const mode = channelCfg ? DI_MODE_NAMES[channelCfg.di[i].mode] || 'Normal' : diModes[i];
    const inv  = channelCfg ? channelCfg.di[i].invert  : false;
    return `<tr>
      <td><span style="${mt}">DI${i}</span></td>
      <td><span class="tag tag-di">Digital In</span></td>
      <td><input name="di_lbl_${i}" class="cfg-input" value="${lbl}"></td>
      <td><select name="di_mode_${i}" class="cfg-select">
        <option${mode==='Normal'?' selected':''}>Normal</option>
        <option${mode==='Counter'?' selected':''}>Counter</option>
        <option${mode==='Frequency'?' selected':''}>Frequency</option>
      </select></td>
      <td style="text-align:center"><input type="checkbox" name="di_inv_${i}"${inv?' checked':''}></td>
      <td><span style="${mt}">GPIO ${DI_PINS[i]}</span></td>
    </tr>`;
  }).join('');
  tb.innerHTML = doRows + diRows;
}

/* ── tabs ── */
async function switchTab(tab, el){
  document.querySelectorAll('.panel').forEach(p=>p.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
  document.getElementById('panel-'+tab).classList.add('active');
  el.classList.add('active');
  if(tab==='config')  loadChannelConfig();
  if(tab==='network'){
    await loadNetworkConfig();
    await loadMqttConfig();
    await loadModbusConfig();
  }
  if(tab==='rs485') loadRs485Config();
}

/* ── pulse ── */
function openPulse(i){ modalCh=i; document.getElementById('modal-ch-label').textContent=doLabels[i]; document.getElementById('modal').classList.add('open'); }
function closeModal(){ document.getElementById('modal').classList.remove('open'); }
function runPulse(){
  const dur=parseInt(document.getElementById('pulse-dur').value)||500;
  const rep=parseInt(document.getElementById('pulse-rep').value)||1;
  closeModal();
  log(`Pulse DO${modalCh}: ${rep}× ${dur}ms`,'ok');
  let c=0;
  function step(){ if(c>=rep*2){setDO(modalCh,false);return;} setDO(modalCh,c%2===0); c++; setTimeout(step,dur); }
  step(); ops++;
}

function toggleWifiFields(){
  const mode = document.getElementById('wifi-mode');
  const fields = document.getElementById('wifi-static-fields');
  if(mode && fields) fields.style.display = mode.value === 'static' ? 'block' : 'none';
}

function toggleWifiSection(){
  const cb    = document.getElementById('wifi-enabled');
  const sec   = document.getElementById('wifi-fields-section');
  const label = document.getElementById('wifi-enabled-label');
  if(!cb || !sec) return;
  const en = cb.checked;
  sec.style.display   = en ? 'block' : 'none';
  if(label) label.textContent = en ? 'ENABLED' : 'DISABLED';
}

function toggleLanFields(){
  const mode = document.getElementById('lan-mode').value;
  const fields = document.getElementById('lan-static-fields');
  if(fields) fields.style.display = mode === 'static' ? 'block' : 'none';
}

async function loadChannelConfig(){
  try {
    const res = await fetch('/api/channel-config');
    if(!res.ok) return;
    channelCfg = await res.json();
    channelCfg.di.forEach((ch, i) => {
      diLabels[i] = ch.label;
      diModes[i]  = DI_MODE_NAMES[ch.mode] || 'Normal';
    });
    channelCfg.do.forEach((ch, i) => { doLabels[i] = ch.label; });
    syncCfg();
    renderRelays(); renderDI();
  } catch(e){ console.error('channel config load failed:', e); }
}

async function saveChannelConfig(){
  const tb = document.getElementById('cfg-tbody');
  if(!tb){ toastSave('Config table not ready'); return; }
  const di = Array.from({length:10}, (_,i) => ({
    label:  (tb.querySelector(`input[name="di_lbl_${i}"]`)  || {}).value || diLabels[i],
    mode:   DI_MODE_NAMES.indexOf((tb.querySelector(`select[name="di_mode_${i}"]`) || {}).value || 'Normal'),
    invert: (tb.querySelector(`input[name="di_inv_${i}"]`)  || {}).checked || false,
  }));
  const doArr = Array.from({length:2}, (_,i) => ({
    label:  (tb.querySelector(`input[name="do_lbl_${i}"]`)  || {}).value || doLabels[i],
    mode:   DO_MODE_NAMES.indexOf((tb.querySelector(`select[name="do_mode_${i}"]`) || {}).value || 'Normal'),
    invert: (tb.querySelector(`input[name="do_inv_${i}"]`)  || {}).checked || false,
  }));
  try {
    const res = await fetch('/api/save-channel-config', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({di, 'do': doArr}),
    });
    if(res.ok){
      di.forEach((ch, i) => { diLabels[i] = ch.label; diModes[i] = DI_MODE_NAMES[ch.mode] || 'Normal'; });
      doArr.forEach((ch, i) => doLabels[i] = ch.label);
      channelCfg = {di, do: doArr};
      renderRelays(); renderDI();
      toastSave('Channel configuration saved');
    } else { toastSave('Save failed', 'err'); }
  } catch(e){ toastSave('Save error', 'err'); }
}

async function resetChannelDefaults(){
  if(!confirm('Reset all channel labels and modes to defaults?')) return;
  const di = Array.from({length:10}, (_,i) => ({label:'Input '+(i+1), mode:0, invert:false}));
  const doArr = [{label:'Relay 0',mode:0,invert:false},{label:'Relay 1',mode:0,invert:false}];
  try {
    const res = await fetch('/api/save-channel-config', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({di, 'do': doArr}),
    });
    if(res.ok){
      di.forEach((ch, i) => { diLabels[i] = ch.label; diModes[i] = 'Normal'; });
      doArr.forEach((ch, i) => doLabels[i] = ch.label);
      channelCfg = {di, do: doArr};
      syncCfg(); renderRelays(); renderDI();
      toastSave('Defaults restored');
    }
  } catch(e){ toastSave('Reset error', 'err'); }
}

async function resetCounter(ch){
  try {
    await fetch('/api/reset-counter?ch=' + ch);
    if(ch === -1) diCounts.fill(0); else diCounts[ch] = 0;
    renderDI();
    log('Counter DI' + ch + ' reset', 'ok');
  } catch(e){ log('Counter reset failed', 'err'); }
}

async function saveWifiConfig(){
  const ssid = document.getElementById('wifi-ssid').value.trim();
  const password = document.getElementById('wifi-password').value;
  const mode = document.getElementById('wifi-mode').value;
  const ip = document.getElementById('wifi-ip').value.trim();
  const gateway = document.getElementById('wifi-gateway').value.trim();

  if(!ssid){
    toastSave('SSID is required', 'warn');
    return;
  }

  if(mode === 'static' && (!ip || !gateway)){
    toastSave('Static IP and Gateway are required', 'warn');
    return;
  }

  const subnet = document.getElementById('wifi-subnet').value.trim();
  const dns = document.getElementById('wifi-dns').value.trim();
  const wifi_enabled = (document.getElementById('wifi-enabled') || {}).checked || false;
  const payload = { ssid, password, mode, ip, subnet, gateway, dns, wifi_enabled };

  try {
    const response = await fetch('/api/save-wifi-config', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify(payload)
    });
    if(response.ok){
      toastSave('Wi-Fi saved — restarting service...');
    } else {
      toastSave('Failed to save Wi-Fi', 'err');
    }
  } catch (error) {
    toastSave('Wi-Fi save error', 'err');
    console.error('Wi-Fi save error:', error);
  }
}

async function saveLanConfig(){
  const mode = document.getElementById('lan-mode').value;
  const ip = document.getElementById('lan-ip').value.trim();
  const gateway = document.getElementById('lan-gateway').value.trim();

  if(mode === 'static' && (!ip || !gateway)){
    toastSave('Static IP and Gateway are required', 'warn');
    return;
  }

  const subnet = document.getElementById('lan-subnet').value.trim();
  const dns = document.getElementById('lan-dns').value.trim();
  const payload = { mode, ip, subnet, gateway, dns };

  try {
    const response = await fetch('/api/save-lan-config', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify(payload)
    });
    if(response.ok){
      toastSave('LAN saved — restarting service...');
    } else {
      toastSave('Failed to save LAN', 'err');
    }
  } catch (error) {
    toastSave('LAN save error', 'err');
    console.error('LAN save error:', error);
  }
}

async function loadNetworkConfig(){
  try {
    const res = await fetch('/api/network-config');
    if(!res.ok) return;
    const cfg = await res.json();

    const wifiEn = document.getElementById('wifi-enabled');
    if(wifiEn){ wifiEn.checked = cfg.wifi_enabled || false; toggleWifiSection(); }
    document.getElementById('wifi-ssid').value    = cfg.wifi_ssid    || '';
    document.getElementById('wifi-password').value = '';
    const wmode = document.getElementById('wifi-mode');
    if(wmode) wmode.value = cfg.wifi_static ? 'static' : 'dhcp';
    document.getElementById('wifi-ip').value      = cfg.wifi_ip      || '';
    document.getElementById('wifi-subnet').value  = cfg.wifi_subnet  || '255.255.255.0';
    document.getElementById('wifi-gateway').value = cfg.wifi_gateway || '';
    document.getElementById('wifi-dns').value     = cfg.wifi_dns     || '';
    toggleWifiFields();

    const lmode = document.getElementById('lan-mode');
    if(lmode) lmode.value = cfg.lan_static ? 'static' : 'dhcp';
    document.getElementById('lan-ip').value       = cfg.lan_ip       || '';
    document.getElementById('lan-subnet').value   = cfg.lan_subnet   || '255.255.255.0';
    document.getElementById('lan-gateway').value  = cfg.lan_gateway  || '';
    document.getElementById('lan-dns').value      = cfg.lan_dns      || '';
    toggleLanFields();
  } catch (error) {
    console.error('Failed loading network config:', error);
  }
}

function toggleMqttSection(){
  const cb    = document.getElementById('mqtt-enabled');
  const sec   = document.getElementById('mqtt-fields-section');
  const label = document.getElementById('mqtt-enabled-label');
  if(!cb || !sec) return;
  const en = cb.checked;
  sec.style.display = en ? 'block' : 'none';
  if(label) label.textContent = en ? 'ENABLED' : 'DISABLED';
}

async function loadMqttConfig(){
  try {
    const res = await fetch('/api/mqtt-config');
    if(!res.ok) return;
    const cfg = await res.json();
    const en = document.getElementById('mqtt-enabled');
    if(en){ en.checked = cfg.enabled || false; toggleMqttSection(); }
    document.getElementById('mqtt-broker').value   = cfg.broker   || '';
    document.getElementById('mqtt-port').value     = cfg.port     || 1883;
    document.getElementById('mqtt-topic').value    = cfg.topic    || 'esp32/status';
    document.getElementById('mqtt-interval').value = cfg.interval || 1000;
    const st = document.getElementById('mqtt-conn-status');
    if(st){
      st.textContent = cfg.connected ? 'CONNECTED' : 'DISCONNECTED';
      st.className = 'info-val ' + (cfg.connected ? 'green' : 'amber');
    }
  } catch (error) {
    console.error('Failed loading MQTT config:', error);
  }
}

async function saveMqttConfig(){
  const broker = document.getElementById('mqtt-broker').value.trim();
  const enabled = (document.getElementById('mqtt-enabled') || {}).checked || false;

  if(enabled && !broker){
    toastSave('Broker is required', 'warn');
    return;
  }

  const port = parseInt(document.getElementById('mqtt-port').value) || 1883;
  const topic = document.getElementById('mqtt-topic').value.trim() || 'esp32/status';
  const interval = parseInt(document.getElementById('mqtt-interval').value) || 1000;
  const payload = { enabled, broker, port, topic, interval };

  try {
    const response = await fetch('/api/save-mqtt-config', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify(payload)
    });
    if(response.ok){
      toastSave('MQTT saved — restarting service...');
    } else {
      toastSave('Failed to save MQTT', 'err');
    }
  } catch (error) {
    toastSave('MQTT save error', 'err');
    console.error('MQTT save error:', error);
  }
}

function toggleModbusSection(){
  const cb    = document.getElementById('modbus-enabled');
  const sec   = document.getElementById('modbus-fields-section');
  const label = document.getElementById('modbus-enabled-label');
  if(!cb || !sec) return;
  const en = cb.checked;
  sec.style.display = en ? 'block' : 'none';
  if(label) label.textContent = en ? 'ENABLED' : 'DISABLED';
}

async function loadModbusConfig(){
  try {
    const res = await fetch('/api/modbus-config');
    if(!res.ok) return;
    const cfg = await res.json();
    const en = document.getElementById('modbus-enabled');
    if(en){ en.checked = cfg.enabled || false; toggleModbusSection(); }
    document.getElementById('modbus-port').value = cfg.port    || 502;
    document.getElementById('modbus-unit').value = cfg.unit_id || 1;
    const st = document.getElementById('modbus-run-status');
    if(st){
      st.textContent = cfg.running ? 'RUNNING' : 'STOPPED';
      st.className = 'info-val ' + (cfg.running ? 'green' : 'amber');
    }
  } catch (error) {
    console.error('Failed loading Modbus config:', error);
  }
}

async function saveModbusConfig(){
  const enabled = (document.getElementById('modbus-enabled') || {}).checked || false;
  const port    = parseInt(document.getElementById('modbus-port').value) || 502;
  const unit_id = parseInt(document.getElementById('modbus-unit').value) || 1;
  const payload = { enabled, port, unit_id };

  try {
    const response = await fetch('/api/save-modbus-config', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify(payload)
    });
    if(response.ok){
      toastSave('Modbus saved — restarting service...');
    } else {
      toastSave('Failed to save Modbus', 'err');
    }
  } catch (error) {
    toastSave('Modbus save error', 'err');
    console.error('Modbus save error:', error);
  }
}

function toastSave(msg='Saved', type='ok'){
  log(msg, type);
  let container = document.getElementById('toast-container');
  if(!container){
    container = document.createElement('div');
    container.id = 'toast-container';
    document.body.appendChild(container);
  }
  const icons = {ok:'✓', err:'✕', warn:'!'};
  const t = document.createElement('div');
  t.className = 'toast toast-' + type;
  t.innerHTML = `<span class="toast-icon">${icons[type]||'✓'}</span><span>${msg}</span>`;
  container.appendChild(t);
  requestAnimationFrame(() => { requestAnimationFrame(() => t.classList.add('show')); });
  setTimeout(() => {
    t.classList.remove('show');
    setTimeout(() => t.remove(), 250);
  }, 3000);
}

async function rebootDevice(){
  if(!confirm('Restart the pl-connect service now?')) return;
  try {
    await fetch('/api/reboot', {method:'POST'});
  } catch(e) {}
  log('Restart command sent — reconnecting in 8 s…', 'warn');
  setTimeout(() => location.reload(), 8000);
}

/* ── clock / uptime ── */
function tick(){
  const now=new Date();
  document.getElementById('clock').textContent=now.toTimeString().slice(0,8);
  const up=Math.floor((Date.now()-startTime)/1000);
  const h=String(Math.floor(up/3600)).padStart(2,'0');
  const m=String(Math.floor(up%3600/60)).padStart(2,'0');
  const s=String(up%60).padStart(2,'0');
  const uel=document.getElementById('uptime'); if(uel) uel.textContent=`${h}:${m}:${s}`;
  const hel=document.getElementById('heap'); if(hel) hel.textContent=(95+Math.random()*5).toFixed(1)+' KB';
  const rel=document.getElementById('rssi'); if(rel) rel.textContent=(-60+Math.floor(Math.random()*8))+' dBm';
}

function applyState(data){
  updateNetworkInfo(data);
  data.inputs.forEach((v, i) => diStates[i] = v);
  data.relays.forEach((v, i) => doStates[i] = v);
  if(data.counts) data.counts.forEach((v, i) => diCounts[i] = v);
  renderDI(); renderRelays(); updateSummary();
  if(data.modbus_rtu) renderModbusRtu(data.modbus_rtu);
}

/* ── RS485 / Modbus RTU master ── */
function renderModbusRtu(slaves){
  const tb = document.getElementById('rtu-rows');
  if(!tb) return;
  /* last_update_ms is the device's boot-relative monotonic clock, not wall-clock
   * epoch time — show the browser's current time at render instead, since a
   * push only arrives right after a fresh poll of that slave. */
  const ts = new Date().toTimeString().slice(0,8);
  const rows = [];
  slaves.forEach(s => {
    const statusHtml = `<span class="info-val ${s.online?'green':'amber'}">${s.online?'ONLINE':'STALE'}</span>`;
    const tsHtml = `<span style="font-family:var(--font-mono);font-size:9px;color:var(--text3)">${s.online?ts:'—'}</span>`;
    if(!s.registers || !s.registers.length){
      rows.push(`<tr>
        <td><span style="font-family:var(--font-mono);font-size:9px">ID ${s.id} ${s.label}</span></td>
        <td colspan="2" style="color:var(--text3)">No registers configured</td>
        <td>${statusHtml}</td>
        <td>${tsHtml}</td>
      </tr>`);
      return;
    }
    s.registers.forEach(r => {
      rows.push(`<tr>
        <td><span style="font-family:var(--font-mono);font-size:9px">ID ${s.id} ${s.label}</span></td>
        <td>${r.label}</td>
        <td><span style="font-family:var(--font-mono);font-size:10px">${Number(r.value).toFixed(2)}${r.unit||''}</span></td>
        <td>${statusHtml}</td>
        <td>${tsHtml}</td>
      </tr>`);
    });
  });
  tb.innerHTML = rows.length ? rows.join('') : '<tr><td colspan="5" style="text-align:center;color:var(--text3)">No data yet</td></tr>';
}

function toggleRtuSection(){
  const cb    = document.getElementById('rtu-enabled');
  const sec   = document.getElementById('rtu-fields-section');
  const label = document.getElementById('rtu-enabled-label');
  if(!cb || !sec) return;
  const en = cb.checked;
  sec.style.display = en ? 'block' : 'none';
  if(label) label.textContent = en ? 'ENABLED' : 'DISABLED';
}

function renderRtuRegisterRows(i){
  const regs = rtuSlaves[i].registers;
  if(!regs.length){
    return '<tr><td colspan="7" style="text-align:center;color:var(--text3)">No registers — click "+ Add Register"</td></tr>';
  }
  return regs.map((r,j) => {
    const isBit = (r.reg_type===2 || r.reg_type===3);
    const dataTypeCell = isBit
      ? `<select class="cfg-select" disabled><option selected>Bit</option></select>`
      : `<select class="cfg-select" onchange="rtuSlaves[${i}].registers[${j}].data_type=parseInt(this.value)">
        <option value="0"${r.data_type===0?' selected':''}>U16</option>
        <option value="1"${r.data_type===1?' selected':''}>S16</option>
        <option value="2"${r.data_type===2?' selected':''}>U32</option>
        <option value="3"${r.data_type===3?' selected':''}>S32</option>
        <option value="4"${r.data_type===4?' selected':''}>F32</option>
      </select>`;
    return `<tr>
      <td><input class="cfg-input" value="${r.label}" onchange="rtuSlaves[${i}].registers[${j}].label=this.value"></td>
      <td><select class="cfg-select" onchange="setRtuRegType(${i},${j},parseInt(this.value))">
        <option value="0"${r.reg_type===0?' selected':''}>Holding</option>
        <option value="1"${r.reg_type===1?' selected':''}>Input</option>
        <option value="2"${r.reg_type===2?' selected':''}>Coil</option>
        <option value="3"${r.reg_type===3?' selected':''}>Discrete</option>
      </select></td>
      <td><input class="cfg-input" style="width:70px" value="${r.start_addr}" onchange="rtuSlaves[${i}].registers[${j}].start_addr=parseInt(this.value)||0"></td>
      <td><input class="cfg-input" style="width:50px" value="${r.unit||''}" placeholder="V" onchange="rtuSlaves[${i}].registers[${j}].unit=this.value"></td>
      <td>${dataTypeCell}</td>
      <td><input class="cfg-input" style="width:60px" value="${r.scale}" onchange="rtuSlaves[${i}].registers[${j}].scale=parseFloat(this.value)||1"></td>
      <td><div class="di-reset-btn" onclick="removeRtuRegisterRow(${i},${j})">DEL</div></td>
    </tr>`;
  }).join('');
}

function setRtuRegType(i, j, rt){
  const r = rtuSlaves[i].registers[j];
  r.reg_type = rt;
  if(rt === 2 || rt === 3){
    r.data_type = 5; /* Bit — forced, Coil/Discrete are single-bit reads */
  } else if(r.data_type === 5){
    r.data_type = 0; /* leaving Coil/Discrete — data_type=Bit is meaningless for Holding/Input */
  }
  renderRtuCfgRows();
}

function renderRtuCfgRows(){
  const list = document.getElementById('rtu-cfg-list');
  if(!list) return;
  if(!rtuSlaves.length){
    list.innerHTML = '<div style="text-align:center;color:var(--text3);padding:12px">No slaves configured — click "+ Add Slave"</div>';
  } else {
    list.innerHTML = rtuSlaves.map((s,i) => `
      <div class="net-form" style="margin-bottom:14px">
        <div class="net-form-title">Slave #${i+1}</div>
        <div class="form-row">
          <span class="form-label">Slave ID</span>
          <input class="cfg-input" style="width:70px" value="${s.slave_id}" onchange="rtuSlaves[${i}].slave_id=parseInt(this.value)||1">
        </div>
        <div class="form-row">
          <span class="form-label">Label</span>
          <input class="cfg-input" value="${s.label}" onchange="rtuSlaves[${i}].label=this.value">
        </div>
        <div class="form-row">
          <span class="form-label">Poll Interval (ms)</span>
          <input class="cfg-input" style="width:90px" value="${s.poll_interval_ms}" onchange="rtuSlaves[${i}].poll_interval_ms=parseInt(this.value)||2000">
        </div>
        <table class="cfg-table">
          <thead><tr><th>Label</th><th>Area</th><th>Start Addr</th><th>Unit</th><th>Type</th><th>Scale</th><th></th></tr></thead>
          <tbody>${renderRtuRegisterRows(i)}</tbody>
        </table>
        <div style="display:flex;gap:10px;margin-top:10px">
          <button class="save-btn" onclick="addRtuRegisterRow(${i})"${s.registers.length>=RTU_MAX_REGS?' disabled':''}>+ Add Register</button>
          <button class="di-reset-btn" onclick="removeRtuSlaveRow(${i})">Remove Slave</button>
        </div>
      </div>`).join('');
  }
  const addBtn = document.getElementById('rtu-add-btn');
  if(addBtn) addBtn.disabled = rtuSlaves.length >= RTU_MAX_SLAVES;
}

function addRtuSlaveRow(){
  if(rtuSlaves.length >= RTU_MAX_SLAVES){
    toastSave('Max ' + RTU_MAX_SLAVES + ' slaves reached', 'warn');
    return;
  }
  rtuSlaves.push({
    slave_id: rtuSlaves.length + 1,
    label: 'Slave ' + (rtuSlaves.length + 1),
    poll_interval_ms: 2000,
    registers: [],
  });
  renderRtuCfgRows();
}

function removeRtuSlaveRow(i){
  rtuSlaves.splice(i, 1);
  renderRtuCfgRows();
}

function addRtuRegisterRow(i){
  const regs = rtuSlaves[i].registers;
  if(regs.length >= RTU_MAX_REGS){
    toastSave('Max ' + RTU_MAX_REGS + ' registers per slave reached', 'warn');
    return;
  }
  regs.push({
    label: 'Reg ' + (regs.length + 1),
    reg_type: 0, start_addr: 0, unit: '', data_type: 0, scale: 1,
  });
  renderRtuCfgRows();
}

function removeRtuRegisterRow(i, j){
  rtuSlaves[i].registers.splice(j, 1);
  renderRtuCfgRows();
}

async function loadRs485Config(){
  try {
    const res = await fetch('/api/rs485-config');
    if(!res.ok) return;
    const cfg = await res.json();
    const en = document.getElementById('rtu-enabled');
    if(en){ en.checked = cfg.enabled || false; toggleRtuSection(); }
    document.getElementById('rtu-baud').value = cfg.baud || 9600;
    const par = document.getElementById('rtu-parity');
    if(par) par.value = cfg.parity || 0;
    const db = document.getElementById('rtu-data-bits');
    if(db) db.value = cfg.data_bits || 8;
    const sb = document.getElementById('rtu-stop-bits');
    if(sb) sb.value = cfg.stop_bits || 0;
    const st = document.getElementById('rtu-run-status');
    if(st){
      st.textContent = cfg.running ? 'RUNNING' : 'STOPPED';
      st.className = 'info-val ' + (cfg.running ? 'green' : 'amber');
    }
    rtuSlaves = (cfg.slaves || []).filter(s => s.enabled).map(s => ({
      slave_id: s.slave_id, label: s.label, poll_interval_ms: s.poll_interval_ms,
      registers: (s.registers || []).map(r => ({
        label: r.label, reg_type: r.reg_type, start_addr: r.start_addr,
        unit: r.unit || '', data_type: r.data_type, scale: r.scale,
      })),
    }));
    renderRtuCfgRows();
  } catch (error) {
    console.error('Failed loading RS485 config:', error);
  }
}

async function saveRs485Config(){
  const enabled   = (document.getElementById('rtu-enabled') || {}).checked || false;
  const baud      = parseInt(document.getElementById('rtu-baud').value) || 9600;
  const parity    = parseInt(document.getElementById('rtu-parity').value) || 0;
  const data_bits = parseInt(document.getElementById('rtu-data-bits').value) || 8;
  const stop_bits = parseInt(document.getElementById('rtu-stop-bits').value) || 0;

  const slaves = rtuSlaves.map(s => ({
    enabled: true,
    slave_id: s.slave_id, label: s.label, poll_interval_ms: s.poll_interval_ms,
    registers: s.registers.map(r => ({
      label: r.label, reg_type: r.reg_type, start_addr: r.start_addr,
      unit: r.unit, data_type: r.data_type, scale: r.scale,
    })),
  }));
  const payload = { enabled, baud, parity, data_bits, stop_bits, slaves };

  try {
    const response = await fetch('/api/save-rs485-config', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify(payload)
    });
    if(response.ok){
      toastSave('RS485 config saved — restarting service...');
    } else {
      toastSave('Failed to save RS485 config', 'err');
    }
  } catch (error) {
    toastSave('RS485 save error', 'err');
    console.error('RS485 save error:', error);
  }
}

/* ── WebSocket ── */
let ws = null;
let wsDelay = 1000;

function connectWS(){
  const url = 'ws://' + location.host + '/ws';
  ws = new WebSocket(url);

  ws.onopen = function(){
    wsDelay = 1000;
    log('Live updates active','ok');
  };

  ws.onmessage = function(e){
    try { applyState(JSON.parse(e.data)); } catch(_){}
  };

  ws.onclose = function(){
    ws = null;
    log('Connection lost — reconnecting in ' + (wsDelay/1000).toFixed(0) + ' s…','warn');
    setTimeout(connectWS, wsDelay);
    wsDelay = Math.min(wsDelay * 2, 30000);
  };

  ws.onerror = function(){ ws.close(); };
}

async function loadAllPages(){
  const pages = ['io','config','network','system','rs485'];
  for(const p of pages){
    try {
      const res = await fetch('/page/'+p);
      if(res.ok) document.getElementById('panel-'+p).innerHTML = await res.text();
    } catch(e){}
  }
  await loadChannelConfig();   /* must finish before renderDI so modes/labels are ready */
  renderRelays();
  renderDI();
  await loadNetworkConfig();
  await loadMqttConfig();
  await loadModbusConfig();
  await loadRs485Config();
  log('System boot — 10×DI / 2×DO module online','ok');
  connectWS();
  tick();
  setInterval(tick, 1000);
}

loadAllPages();
