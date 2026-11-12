(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const el = (tag, cls, value) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (value) node.textContent = value;
    return node;
  };
  const splits = [1, 4, 6, 9, 16];
  const grid = $('liveGrid');
  const players = new Map();
  const presetKey = 'easynvr-live-presets';
  const selectedPresetKey = 'easynvr-live-selected-preset';
  let channels = new Map(), split = 4, slots = [], selected = 0, pickIndex = 0;
  let layouts = new Map(), undo = null, initialized = false, refreshing = false, cruise = null;
  let presets = [];
  let selectedPreset = '';
  try {
    const saved = JSON.parse(localStorage.getItem(presetKey) || '[]');
    if (Array.isArray(saved)) presets = saved.filter(p => typeof p?.name === 'string' && splits.includes(p.split) && Array.isArray(p.slots)).slice(0, 20);
    selectedPreset = localStorage.getItem(selectedPresetKey) || '';
  } catch { /* A missing browser store does not prevent playback. */ }

  function message(text) { $('liveMessage').textContent = text; }
  function button(label, action) {
    const node = el('button', '', label);
    node.type = 'button';
    node.addEventListener('click', action);
    return node;
  }
  function updateStatus() {
    const online = [...players.values()].filter(p => p.playing).length;
    $('liveSummary').textContent = split === 1
      ? `${channels.size} 路摄像头 · 主画面：${channels.get(slots[0])?.name || '未选择'} · ${online} 路正在播放`
      : `${channels.size} 路摄像头 · ${slots.filter(Boolean).length} / ${split} 个画面 · ${online} 路正在播放`;
    $('liveSnapshot').disabled = !players.get(slots[selected])?.playing;
    $('liveCruise').disabled = !cruise && online < 2 && !(split === 1 && [...channels.values()].filter(c => c.stream).length > 1);
  }
  function select(index) {
    selected = index;
    [...grid.children].forEach((tile, i) => tile.classList.toggle('is-selected', index === i));
    updateStatus();
  }
  function dispose(player) {
    clearTimeout(player.retry);
    clearTimeout(player.deadline);
    player.retry = player.deadline = null;
    player.video.onplaying = player.video.onwaiting = player.video.onerror = null;
    player.hls?.destroy();
    player.hls = null;
    player.video.pause();
    player.video.removeAttribute('src');
    player.video.load();
  }
  function connect(player) {
    const { channel, video, state } = player;
    if (!channel.stream) { state.textContent = '未接入本机 HLS'; return; }
    dispose(player);
    player.playing = false;
    state.hidden = false;
    state.textContent = '正在连接…';
    const retry = () => {
      if (player.retry || players.get(channel.id) !== player) return;
      clearTimeout(player.deadline);
      player.playing = false;
      state.hidden = false;
      state.textContent = '直播暂不可用，正在重试';
      updateStatus();
      player.retry = setTimeout(() => { player.retry = null; connect(player); }, 8000);
    };
    const play = () => video.play().catch(error => {
      if (error.name === 'NotAllowedError') {
        clearTimeout(player.deadline);
        state.hidden = false;
        state.textContent = '点击画面播放';
      }
    });
    video.onplaying = () => {
      clearTimeout(player.retry); player.retry = null;
      clearTimeout(player.deadline);
      player.playing = true;
      state.hidden = true;
      updateStatus();
    };
    video.onwaiting = () => {
      clearTimeout(player.deadline);
      player.deadline = setTimeout(retry, 15000);
    };
    video.onerror = retry;
    if (window.Hls?.isSupported()) {
      player.hls = new Hls({ enableWorker: false, backBufferLength: 8, maxBufferLength: 15, liveSyncDurationCount: 3 });
      player.hls.on(Hls.Events.MANIFEST_PARSED, play);
      player.hls.on(Hls.Events.ERROR, (_, data) => { if (data.fatal) retry(); });
      player.hls.loadSource(channel.stream);
      player.hls.attachMedia(video);
    } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
      video.src = channel.stream;
      play();
    } else {
      state.textContent = '当前浏览器无法播放 HLS';
      return;
    }
    player.deadline = setTimeout(retry, 15000);
  }

  function cameraTile(channel) {
    const tile = el('div', 'live-tile');
    const video = el('video');
    video.muted = true;
    video.playsInline = true;
    video.setAttribute('aria-label', channel.name);
    const state = el('span', 'live-tile-state');
    const head = el('div', 'live-tile-head');
    const name = el('span', 'live-tile-name', channel.name);
    const actions = el('div', 'live-tile-actions');
    const audio = button('静音', event => {
      event.stopPropagation();
      video.muted = !video.muted;
      audio.textContent = video.muted ? '静音' : '有声';
      audio.setAttribute('aria-pressed', String(!video.muted));
    });
    audio.setAttribute('aria-label', `${channel.name} 声音`);
    audio.setAttribute('aria-pressed', 'false');
    const remove = button('移除', event => {
      event.stopPropagation();
      stopCruise();
      slots[slots.indexOf(channel.id)] = null;
      draw();
    });
    remove.setAttribute('aria-label', `移除 ${channel.name}`);
    actions.append(audio, remove);
    head.append(name, actions);
    tile.append(video, state, head);
    tile.addEventListener('click', () => {
      select(slots.indexOf(channel.id));
      if (video.paused && video.readyState >= 2) video.play().catch(() => {});
    });
    tile.addEventListener('dblclick', event => {
      if (split === 1 || event.target.closest('button')) return;
      select(slots.indexOf(channel.id));
      setSplit(1);
    });
    const player = { channel, tile, video, name, state, audio, remove, playing: false };
    players.set(channel.id, player);
    connect(player);
    return player;
  }
  function emptyTile(index) {
    const tile = el('div', 'live-tile');
    tile.dataset.emptySlot = index;
    const add = button('', () => openPicker(index));
    add.className = 'live-empty';
    const plus = el('span', '', '+');
    plus.setAttribute('aria-hidden', 'true');
    add.append(plus, document.createTextNode('添加摄像头'));
    add.setAttribute('aria-label', `在画面 ${index + 1} 添加摄像头`);
    tile.append(add);
    return tile;
  }
  function draw() {
    grid.dataset.split = split;
    if (split === 1) return drawSingle();
    for (const shell of grid.querySelectorAll(':scope > .live-single')) shell.remove();
    const kept = new Set(slots.filter(Boolean));
    for (const [id, player] of players) {
      if (!kept.has(id) || channels.get(id)?.stream !== player.channel.stream) {
        players.delete(id);
        dispose(player);
        player.tile.remove();
      }
    }
    slots.forEach((id, i) => {
      const channel = channels.get(id);
      let tile = grid.children[i];
      if (channel) {
        const player = players.get(id) || cameraTile(channel);
        player.tile.classList.remove('live-thumb', 'live-tile-main');
        player.name.textContent = channel.name;
        player.video.setAttribute('aria-label', channel.name);
        player.audio.setAttribute('aria-label', `${channel.name} 声音`);
        player.remove.setAttribute('aria-label', `移除 ${channel.name}`);
        player.remove.hidden = false;
        tile = player.tile;
      } else if (tile?.dataset.emptySlot !== String(i)) tile = emptyTile(i);
      if (grid.children[i] !== tile) grid.insertBefore(tile, grid.children[i] || null);
    });
    while (grid.children.length > split) grid.lastElementChild.remove();
    document.querySelectorAll('.live-splits button').forEach(btn => btn.setAttribute('aria-pressed', String(Number(btn.dataset.split) === split)));
    select(Math.min(selected, split - 1));
  }

  function switchMain(id) {
    if (!channels.has(id) || slots[0] === id) return;
    stopCruise();
    slots[0] = id;
    selected = 0;
    draw();
    message('主画面：' + channels.get(id).name);
  }

  function drawSingle() {
    const mainId = slots[0] || null;
    const rest = [...channels.values()].filter(c => c.id !== mainId);
    const kept = new Set([mainId, ...rest.map(c => c.id)].filter(Boolean));
    for (const [id, player] of players) {
      if (!kept.has(id) || channels.get(id)?.stream !== player.channel.stream) {
        players.delete(id);
        dispose(player);
        player.tile.remove();
      }
    }
    let shell = grid.querySelector(':scope > .live-single');
    if (!shell) {
      grid.replaceChildren();
      shell = el('div', 'live-single');
      const main = el('div', 'live-single-main');
      const rail = el('div', 'live-single-rail');
      main.id = 'liveMain'; rail.id = 'liveRail'; rail.setAttribute('role', 'list');
      shell.append(main, rail);
      grid.append(shell);
    }
    const mainBox = shell.firstElementChild, rail = shell.lastElementChild;
    const channel = channels.get(mainId);
    if (channel) {
      delete mainBox.dataset.empty;
      const player = players.get(mainId) || cameraTile(channel);
      player.tile.classList.remove('live-thumb');
      player.tile.classList.add('live-tile-main');
      player.name.textContent = channel.name;
      player.video.setAttribute('aria-label', channel.name);
      player.audio.setAttribute('aria-label', `${channel.name} 声音`);
      player.remove.setAttribute('aria-label', `移除 ${channel.name}`);
      player.remove.hidden = false;
      player.tile.onclick = () => { select(0); if (player.video.paused && player.video.readyState >= 2) player.video.play().catch(() => {}); };
      player.tile.ondblclick = null;
      if (mainBox.firstElementChild !== player.tile) mainBox.replaceChildren(player.tile);
    } else if (mainBox.dataset.empty !== '1') {
      mainBox.dataset.empty = '1';
      mainBox.replaceChildren(emptyTile(0));
    }
    rail.replaceChildren(...rest.map(camera => {
      const player = players.get(camera.id) || cameraTile(camera);
      player.tile.classList.add('live-thumb');
      player.tile.classList.remove('live-tile-main');
      player.tile.dataset.camera = camera.id;
      player.tile.tabIndex = 0;
      player.tile.setAttribute('role', 'listitem');
      player.tile.setAttribute('aria-label', '切到主画面：' + camera.name);
      player.tile.title = camera.name;
      player.name.textContent = camera.name;
      player.tile.onclick = () => switchMain(camera.id);
      player.tile.ondblclick = null;
      player.tile.onkeydown = event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); switchMain(camera.id); } };
      return player.tile;
    }));
    document.querySelectorAll('.live-splits button').forEach(btn => btn.setAttribute('aria-pressed', String(Number(btn.dataset.split) === split)));
    select(0);
    requestAnimationFrame(() => {
      if (split !== 1 || !rail.isConnected) return;
      fillRail(rail, rest.length);
    });
  }

  function fillRail(rail, used) {
    for (const node of rail.querySelectorAll('.live-thumb-empty')) node.remove();
    if (!rail.isConnected) return;
    const sample = rail.querySelector('.live-thumb:not(.live-thumb-empty)');
    if (!sample) return;
    const horizontal = getComputedStyle(rail).flexDirection === 'row';
    const railBox = rail.getBoundingClientRect(), cellBox = sample.getBoundingClientRect();
    const size = horizontal ? railBox.width : railBox.height;
    const cell = (horizontal ? cellBox.width : cellBox.height) + 6;
    if (!size || !cell) return;
    const available = size - used * cell;
    if (available < 60) return;
    const count = Math.min(6, Math.max(1, Math.round(available / cell)));
    for (let i = 0; i < count; i++) {
      const add = el('button', 'live-thumb live-thumb-empty', '＋ 添加摄像头');
      add.type = 'button';
      add.setAttribute('aria-label', '添加摄像头到主画面');
      add.style.flex = '1 1 0';
      add.style.aspectRatio = 'auto';
      add.style.minHeight = '0';
      add.style.minWidth = '0';
      add.onclick = () => openPicker(0);
      rail.append(add);
    }
  }

  function sanitizeSlots(values, size) {
    const seen = new Set();
    return Array.from({ length: size }, (_, i) => {
      const id = values[i];
      if (!channels.has(id) || seen.has(id)) return null;
      seen.add(id);
      return id;
    });
  }
  async function refresh(retryPlayers = false) {
    if (refreshing) return;
    refreshing = true;
    try {
      const response = await fetch('/api/live', { cache: 'no-store' });
      if (!response.ok) throw Error('无法读取摄像头清单');
      const data = await response.json();
      channels = new Map(data.channels.map(c => [c.id, c]));
      if (!initialized) {
        initialized = true;
        const preset = presets.find(p => p.name === selectedPreset);
        useLayout(preset || { split, slots: [...channels.keys()] });
      } else {
        slots = sanitizeSlots(slots, split);
        draw();
      }
      if (retryPlayers) for (const player of players.values()) if (!player.playing) { clearTimeout(player.retry); player.retry = null; connect(player); }
      if (retryPlayers) message('已刷新摄像头清单');
      if ($('livePicker').open) renderOptions();
    } catch (error) {
      message(error.message);
      if (!initialized) $('liveSummary').textContent = '摄像头清单暂不可用';
    } finally { refreshing = false; }
  }

  function renderOptions() {
    const search = $('liveSearch').value.trim().toLocaleLowerCase();
    const options = [...channels.values()].filter(c => c.name.toLocaleLowerCase().includes(search));
    $('liveOptions').replaceChildren(...options.map(camera => {
      const index = slots.indexOf(camera.id);
      const option = button(camera.name, () => {
        stopCruise();
        if (index >= 0) slots[index] = slots[pickIndex];
        slots[pickIndex] = camera.id;
        selected = pickIndex;
        $('livePicker').close();
        draw();
      });
      option.append(el('small', '', index >= 0 ? `画面 ${index + 1}` : camera.stream ? 'HLS' : '未接入'));
      return option;
    }));
    if (!options.length) $('liveOptions').append(el('p', '', '没有匹配的摄像头'));
  }
  function openPicker(index) {
    pickIndex = index;
    $('livePickerTitle').textContent = split === 1 ? '选择主画面摄像头' : `选择摄像头 · 画面 ${index + 1}`;
    $('liveSearch').value = '';
    renderOptions();
    $('livePicker').showModal();
    $('liveSearch').focus();
  }
  function stopCruise() {
    clearInterval(cruise);
    cruise = null;
    $('liveCruise').textContent = '轮巡';
    $('liveCruise').setAttribute('aria-pressed', 'false');
  }
  function setSplit(next) {
    if (next === split) return;
    stopCruise();
    layouts.set(split, slots.slice());
    const source = next === 1 ? [slots[selected] || slots.find(Boolean)] : layouts.get(next) || slots;
    split = next;
    slots = sanitizeSlots(source, split);
    selected = Math.max(0, slots.findIndex(Boolean));
    draw();
  }
  function captureLayout() { return { split, slots: slots.slice() }; }
  function useLayout(layout) {
    stopCruise();
    split = layout.split;
    slots = sanitizeSlots(layout.slots, split);
    selected = Math.max(0, slots.findIndex(Boolean));
    draw();
  }
  function renderPresets() {
    const list = $('livePreset');
    list.replaceChildren(new Option('选择预设', ''), ...presets.map(p => new Option(p.name, p.name)));
    list.value = presets.some(p => p.name === selectedPreset) ? selectedPreset : '';
    $('liveDelete').disabled = !list.value;
  }
  function savePresets() {
    try { localStorage.setItem(presetKey, JSON.stringify(presets)); return true; }
    catch { message('浏览器无法长期保存，本次页面仍可使用预设'); return false; }
  }
  function rememberPreset(name) {
    selectedPreset = name;
    try {
      if (name) localStorage.setItem(selectedPresetKey, name);
      else localStorage.removeItem(selectedPresetKey);
    } catch { message('浏览器无法保存预设选择'); }
  }

  $('liveRefresh').onclick = () => refresh(true);
  $('liveCamera').onclick = () => openPicker(selected);
  $('liveSearch').oninput = renderOptions;
  document.querySelectorAll('.live-splits button').forEach(btn => { btn.onclick = () => setSplit(Number(btn.dataset.split)); });
  $('liveClear').onclick = () => {
    undo = captureLayout();
    useLayout({ split, slots: [] });
    $('liveUndo').hidden = false;
    message('已清空视频墙');
  };
  $('liveUndo').onclick = () => {
    if (undo) useLayout(undo);
    undo = null;
    $('liveUndo').hidden = true;
    message('已恢复清屏前的布局');
  };
  $('liveCruise').onclick = () => {
    if (cruise) { stopCruise(); updateStatus(); message('已停止轮巡'); return; }
    $('liveCruise').textContent = '停止轮巡';
    $('liveCruise').setAttribute('aria-pressed', 'true');
    cruise = setInterval(() => {
      const active = [...players.values()].filter(p => p.playing).map(p => p.channel.id);
      const ids = split === 1 ? [...channels.values()].filter(c => c.stream).map(c => c.id) : slots.filter(id => active.includes(id));
      if (ids.length < 2) { stopCruise(); updateStatus(); message('可轮巡的画面不足'); return; }
      const next = ids[(ids.indexOf(slots[selected]) + 1) % ids.length];
      if (split === 1) { slots[0] = next; draw(); } else select(slots.indexOf(next));
    }, split === 1 ? 12000 : 5000);
    message(split === 1 ? '每 12 秒切换一路直播' : '每 5 秒选中下一路正在播放的画面');
  };
  $('liveFullscreen').onclick = async () => {
    try { if (document.fullscreenElement) await document.exitFullscreen(); else await $('livePage').requestFullscreen(); }
    catch { message('当前浏览器无法进入全屏'); }
  };
  document.addEventListener('fullscreenchange', () => { $('liveFullscreen').textContent = document.fullscreenElement ? '退出全屏' : '全屏'; });
  $('liveSnapshot').onclick = () => {
    const player = players.get(slots[selected]);
    if (!player?.video.videoWidth) return;
    const canvas = el('canvas');
    canvas.width = player.video.videoWidth;
    canvas.height = player.video.videoHeight;
    canvas.getContext('2d').drawImage(player.video, 0, 0);
    canvas.toBlob(blob => {
      if (!blob) { message('当前画面无法抓图'); return; }
      const link = el('a');
      link.href = URL.createObjectURL(blob);
      link.download = `${player.name.textContent.replace(/[<>:"/\\|?*]/g, '_')}_${Date.now()}.jpg`;
      link.click();
      setTimeout(() => URL.revokeObjectURL(link.href), 2000);
      message('已保存当前画面');
    }, 'image/jpeg', .92);
  };
  $('liveSave').onclick = () => { $('livePresetName').value = ''; $('liveSaveDialog').showModal(); $('livePresetName').focus(); };
  $('liveSaveForm').onsubmit = event => {
    if (event.submitter?.value === 'cancel') return;
    event.preventDefault();
    const name = $('livePresetName').value.trim();
    if (!name) return;
    presets = [{ name, ...captureLayout() }, ...presets.filter(p => p.name !== name)].slice(0, 20);
    if (savePresets()) message('已保存布局预设');
    rememberPreset(name);
    renderPresets();
    $('liveSaveDialog').close();
  };
  $('livePreset').onchange = () => {
    const preset = presets.find(p => p.name === $('livePreset').value);
    rememberPreset(preset?.name || '');
    $('liveDelete').disabled = !preset;
    if (preset) useLayout(preset);
  };
  $('liveDelete').onclick = () => {
    presets = presets.filter(p => p.name !== $('livePreset').value);
    if (savePresets()) message('已删除布局预设');
    rememberPreset('');
    renderPresets();
  };
  window.addEventListener('pagehide', () => {
    stopCruise();
    for (const player of players.values()) dispose(player);
  });
  window.addEventListener('resize', () => {
    if (split !== 1) return;
    const rail = document.querySelector('.live-single-rail');
    if (rail) fillRail(rail, rail.querySelectorAll('.live-thumb:not(.live-thumb-empty)').length);
  });
  window.addEventListener('pageshow', event => { if (event.persisted) for (const player of players.values()) connect(player); });
  slots = Array(split).fill(null);
  renderPresets();
  draw();
  refresh();
  setInterval(refresh, 30000);
})();
