import { makeZip } from './zip.js';

const $ = (id) => document.getElementById(id);
const MAX_FILES = 20;
const RESULT_BUDGET = 256 * 1024 * 1024;
const descriptions = {
  mp3: 'MP3 适合手机、电脑与大多数音乐播放器。',
  flac: 'FLAC 使用无损编码。源音频若为有损，转换不会提升音质。',
  m4a: 'M4A 使用 AAC 编码，适合常见移动设备。',
  wav: 'WAV 输出 16 位 PCM 音频，文件体积通常较大。',
  ogg: 'Ogg 使用 Vorbis 编码，使用前请确认播放器兼容。',
  source: '提取音轨并保留原编码，输出后缀由识别结果决定。',
};
const labels = { mp3: 'MP3', flac: 'FLAC', m4a: 'M4A', wav: 'WAV', ogg: 'OGG', source: '原始格式' };
const messages = {
  needs_server_config: '当前服务暂未开通此类文件的转换，请尝试其他音频格式。',
  requires_file_key: '此文件版本暂不支持自动转换，请尝试其他音频格式。',
  unsupported_format: '还不支持此文件版本，或文件内容无法识别。',
  invalid_audio: '文件异常或没有可用音轨，请检查原文件。',
  tools_unavailable: '转换服务暂不可用，请稍后重试。',
  busy: '服务正在处理其他文件，请稍后点击重新识别或重试。',
  output_too_large: '转换结果过大，请选择 MP3 等体积较小的输出格式。',
  conversion_failed: '未能生成可播放的音频。文件可能损坏，或此版本暂不可用。',
  processing_timeout: '处理超时，请稍后重试或换一个较小的文件。',
  upload_timeout: '上传超时，请检查连接后重试。',
  temporary_storage_unavailable: '转换服务暂时无法接收文件，请稍后重试。',
};
let items = [];
let sequence = 0;
let inspecting = false;
let busy = false;
let stopRequested = false;
let packing = false;
let serviceReady = false;
let connecting = false;
let capabilities = null;
let noticeTimer;
let activeItem = null;
let batch = null;
const transientUrls = new Set();

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}
function icon(name, small = false) {
  const node = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  node.classList.add('icon');
  if (small) node.classList.add('small');
  node.setAttribute('aria-hidden', 'true');
  const use = document.createElementNS(node.namespaceURI, 'use');
  use.setAttribute('href', `#i-${name}`);
  node.append(use);
  return node;
}
function notice(message) {
  $('notice').textContent = message;
  $('notice').hidden = false;
  clearTimeout(noticeTimer);
  noticeTimer = setTimeout(() => { $('notice').hidden = true; }, 5000);
}
function bytes(size) {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}
function duration(value) {
  const rounded = Math.round(value);
  return value > 0 ? `${Math.floor(rounded / 60)}:${String(rounded % 60).padStart(2, '0')}` : '';
}
function selectedFormat() { return document.querySelector('input[name="format"]:checked').value; }
function readyItems() { return items.filter((item) => item.info?.can_convert && !item.result && ['ready', 'error'].includes(item.state)); }
function retainedSize() { return items.reduce((size, item) => size + (item.result?.blob.size || 0), 0); }
function isLive(item) { return items.includes(item); }
function disposeResult(item) {
  if (item.result) URL.revokeObjectURL(item.result.url);
  item.result = null;
}
function friendly(error) {
  if (error.code === 'upload_too_large') return `文件超过单个 ${bytes(capabilities?.max_upload_bytes || 0)} 的上传限制。`;
  if (error.code === 'memory_limit') return '当前页面的结果已占用较多空间，请先下载并移除已完成文件。';
  if (error.code === 'network') return '连接中断，请检查转换服务后重试。';
  return messages[error.code] || '请求未能完成，请稍后重试。';
}
function fail(code) { return Object.assign(new Error(code), { code }); }

function renderItem(item) {
  const row = item.node;
  row.replaceChildren();
  const failed = ['blocked', 'error'].includes(item.state);
  const working = ['inspecting', 'converting'].includes(item.state);
  const avatar = element('span', `file-avatar${item.result ? ' done' : failed ? ' error' : ''}`);
  avatar.append(icon('note'));
  const content = element('div', 'file-info');
  const title = element('p', 'file-title', item.file.name);
  title.title = item.file.name;
  const meta = element('div', 'file-meta');
  const details = [bytes(item.file.size)];
  if (item.info?.container && item.info.container !== '未知') details.push(item.info.container);
  if (item.info?.duration_seconds) details.push(duration(item.info.duration_seconds));
  details.forEach((text, index) => {
    if (index) meta.append(element('span', 'separator', '·'));
    meta.append(element('span', '', text));
  });
  content.append(title, meta);
  const output = element('div', `file-output${item.result ? ' done' : ''}`);
  const outputText = item.result ? `${item.result.name.split('.').pop().toUpperCase()} · ${bytes(item.result.blob.size)}` : labels[selectedFormat()];
  output.append(icon('arrow'), element('span', '', outputText));
  output.title = item.result ? item.result.name : `目标：${labels[selectedFormat()]}`;
  const track = element('div', 'file-track');
  const status = element('div', `file-status${failed ? ' error' : item.result ? ' done' : item.state === 'ready' ? ' ready' : ''}`);
  if (working) status.append(element('span', 'spinner'));
  const defaultText = { queued: '等待识别', inspecting: '正在自动识别…', ready: '等待转换', done: '转换完成 · 100%' };
  const statusText = element('span', 'file-status-text', item.message || defaultText[item.state] || '');
  statusText.title = statusText.textContent;
  status.append(statusText);
  const progress = element('progress', `file-progress${item.result ? ' done' : ''}`);
  progress.max = 100;
  progress.setAttribute('aria-label', item.state === 'inspecting' ? '文件识别进度' : '当前文件传输进度');
  if (item.result) progress.value = 100;
  else if (!working) progress.value = 0;
  else if (item.percent !== null && item.percent !== undefined) progress.value = item.percent;
  track.append(status, progress);
  const actions = element('div', 'file-actions');
  if (item.result) {
    const download = element('a', 'download-link', '下载');
    download.prepend(icon('download', true));
    download.href = item.result.url;
    download.download = item.result.name;
    download.setAttribute('aria-label', `下载 ${item.result.name}`);
    actions.append(download);
  }
  if (['blocked', 'error', 'done'].includes(item.state)) {
    const again = element('button', 'icon-button');
    const title = item.result ? '重新转换' : item.info?.can_convert ? '重试转换' : '重新识别';
    again.title = title;
    again.setAttribute('aria-label', `${title} ${item.file.name}`);
    again.append(icon('refresh'));
    again.disabled = busy;
    again.addEventListener('click', () => {
      disposeResult(item);
      item.message = '';
      if (item.info?.can_convert) item.state = 'ready';
      else { item.state = 'queued'; item.info = null; }
      renderItem(item);
      refresh();
      inspectQueue();
    });
    actions.append(again);
  }
  const remove = element('button', 'icon-button');
  remove.setAttribute('aria-label', `移除 ${item.file.name}`);
  remove.title = '移除文件';
  remove.append(icon('close'));
  remove.disabled = busy;
  remove.addEventListener('click', () => removeItem(item));
  actions.append(remove);
  row.append(avatar, content, output, track, actions);
}

function renderRecent() {
  const list = $('recent-list');
  list.replaceChildren();
  $('recent-empty').hidden = items.length > 0;
  for (const item of items.slice(-5).reverse()) {
    const row = element('li', 'recent-row');
    const mark = element('span', 'recent-icon');
    mark.append(icon('note'));
    const description = element('div', 'recent-name');
    const title = element('strong', '', item.file.name);
    title.title = item.file.name;
    description.append(title, element('small', '', [bytes(item.file.size), duration(item.info?.duration_seconds)].filter(Boolean).join(' · ')));
    const type = item.info?.codec || item.file.name.split('.').pop() || 'AUDIO';
    const shortType = /^[a-z0-9]{1,8}$/i.test(type) ? type.toUpperCase() : 'AUDIO';
    const badge = element('span', 'format-badge', shortType);
    if (['mp3', 'flac', 'wav', 'm4a', 'aac'].includes(type.toLowerCase())) badge.classList.add(type.toLowerCase());
    row.append(mark, description, badge);
    list.append(row);
  }
}

function filterFiles() {
  const query = $('file-search').value.trim().toLocaleLowerCase();
  let visible = 0;
  for (const item of items) {
    const text = `${item.file.name} ${item.info?.container || ''} ${item.info?.codec || ''}`.toLocaleLowerCase();
    item.node.hidden = Boolean(query && !text.includes(query));
    if (!item.node.hidden) visible += 1;
  }
  $('search-empty').hidden = !items.length || !query || visible > 0;
}

function refresh() {
  const ready = readyItems().length;
  const pending = items.some((item) => ['queued', 'inspecting'].includes(item.state));
  const done = items.filter((item) => item.result).length;
  const blocked = items.filter((item) => item.state === 'blocked').length;
  $('file-count').textContent = String(items.length);
  $('total-size').textContent = bytes(items.reduce((size, item) => size + item.file.size, 0));
  $('clear-completed').disabled = busy || !done;
  renderRecent();
  filterFiles();
  $('ready-count').textContent = String(busy ? batch?.total || 0 : ready);
  $('empty-state').hidden = items.length > 0;
  $('queue-footer').hidden = !items.length;
  $('queue-summary').textContent = [ready ? `${ready} 个待转换` : '', done ? `${done} 个已完成` : '', blocked ? `${blocked} 个暂不支持` : '', pending ? '正在识别文件' : ''].filter(Boolean).join(' · ') || '等待操作';
  $('clear-files').disabled = busy || !items.length;
  $('choose-files').disabled = busy || !serviceReady;
  $('file-input').disabled = busy || !serviceReady;
  $('dropzone').classList.toggle('disabled', busy || !serviceReady);
  $('convert').disabled = busy || pending || !serviceReady || ready === 0;
  $('convert').hidden = busy;
  $('stop').hidden = !busy;
  $('stop').disabled = stopRequested;
  $('download-all').disabled = busy || packing || done < 2;
  $('download-all').querySelector('span').textContent = packing ? '正在打包…' : `打包下载${done > 1 ? ` (${done})` : ''}`;
  document.querySelectorAll('input[name="format"]').forEach((input) => { input.disabled = busy || (capabilities && !Object.hasOwn(capabilities.formats, input.value)); });
  $('bitrate').disabled = busy || !capabilities?.formats[selectedFormat()]?.length;
  $('button-caption').hidden = busy;
  $('button-caption').textContent = pending ? '识别完成后即可开始转换' : ready ? `将依次转换 ${ready} 个文件` : done ? '点击列表中的下载按钮保存音乐' : items.length ? '当前没有可转换的文件' : '先添加音乐文件，再开始转换';
}

function updateFormats() {
  const fmt = selectedFormat();
  $('target-format').textContent = labels[fmt];
  $('format-description').textContent = descriptions[fmt];
  $('quality-hint').textContent = fmt === 'source' ? '无需设置' : capabilities?.formats[fmt]?.length ? '推荐自动' : '无需设置';
  const select = $('bitrate');
  select.replaceChildren(element('option', '', fmt === 'source' ? '保留原编码' : capabilities?.formats[fmt]?.length ? '自动 · 原样或默认码率' : '无需设置码率'));
  select.firstChild.value = '';
  for (const rate of capabilities?.formats[fmt] || []) {
    const option = element('option', '', `${rate.replace('k', '')} kbps`);
    option.value = rate;
    select.append(option);
  }
  $('quality-note').textContent = fmt === 'source' ? '实际输出格式由原始音轨决定。原始文件会保留，不会被覆盖。' : '有损音频转成 FLAC 或 WAV，不会恢复已经损失的音质。';
  items.forEach(renderItem);
  refresh();
}

async function connect() {
  if (connecting || busy) return;
  connecting = true;
  serviceReady = false;
  $('service-status').className = 'service-status';
  $('service-label').textContent = '连接服务中';
  $('reconnect').disabled = true;
  refresh();
  const abort = new AbortController();
  const timer = setTimeout(() => abort.abort(), 8000);
  try {
    const [health, response] = await Promise.all(['/healthz', '/api/v1/capabilities'].map((url) => fetch(url, { signal: abort.signal, cache: 'no-store' })));
    if (!health.ok || !response.ok) throw fail('network');
    const data = await response.json();
    if (!(data.max_upload_bytes > 0) || !(data.max_output_bytes > 0) || !data.formats?.mp3) throw fail('network');
    capabilities = data;
    serviceReady = true;
    $('service-status').className = 'service-status connected';
    $('service-label').textContent = '转换服务已连接';
    $('connection-banner').hidden = true;
    $('upload-limit').textContent = `单个文件 ≤ ${bytes(data.max_upload_bytes)} · 最多 ${MAX_FILES} 个`;
    updateFormats();
  } catch {
    $('service-status').className = 'service-status disconnected';
    $('service-label').textContent = '服务未连接';
    $('connection-banner').hidden = false;
    $('connection-message').textContent = '无法连接转换服务，请确认服务已启动后重新连接。';
    $('upload-limit').textContent = '请先连接转换服务';
  } finally {
    clearTimeout(timer);
    connecting = false;
    $('reconnect').disabled = false;
    refresh();
    if (serviceReady) inspectQueue();
  }
}

function addFiles(files) {
  if (busy) { notice('正在转换，请在本批完成后再添加文件。'); return; }
  if (!serviceReady) { notice('请先连接转换服务。'); return; }
  let duplicates = 0;
  let limited = 0;
  for (const file of files) {
    if (!(file instanceof File)) continue;
    const identity = `${file.name}\u0000${file.size}\u0000${file.lastModified}`;
    if (items.some((item) => item.identity === identity)) { duplicates += 1; continue; }
    if (items.length >= MAX_FILES) { limited += 1; continue; }
    const item = { id: ++sequence, file, identity, state: 'queued', info: null, result: null, message: '', percent: null, xhr: null };
    item.node = element('li', 'file-row');
    item.node.dataset.fileId = item.id;
    if (!file.size || file.size > capabilities.max_upload_bytes) {
      item.state = 'blocked';
      item.message = file.size ? `超过单个 ${bytes(capabilities.max_upload_bytes)} 的上传限制` : '这是一个空文件，无法转换';
    }
    items.push(item);
    $('file-list').append(item.node);
    renderItem(item);
  }
  if (limited) notice(`一次最多保留 ${MAX_FILES} 个文件，已跳过 ${limited} 个。`);
  else if (duplicates) notice(`已跳过 ${duplicates} 个重复文件。`);
  refresh();
  inspectQueue();
}

function removeItem(item) {
  if (busy) return;
  items = items.filter((entry) => entry !== item);
  item.xhr?.abort();
  disposeResult(item);
  item.node.remove();
  refresh();
}

function postFile(item, endpoint, params, converting = false) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    item.xhr = xhr;
    let budgetExceeded = false;
    xhr.open('POST', `/api/v1/${endpoint}?${new URLSearchParams(params)}`);
    xhr.responseType = 'blob';
    xhr.timeout = converting ? 330000 : 95000;
    xhr.setRequestHeader('Content-Type', 'application/octet-stream');
    if (converting) xhr.setRequestHeader('Accept', 'application/octet-stream');
    const update = (message, percent = null) => {
      if (!isLive(item)) return;
      item.message = message;
      item.percent = percent;
      const statusText = item.node.querySelector('.file-status-text');
      if (statusText) { statusText.textContent = message; statusText.title = message; }
      const progress = item.node.querySelector('.file-progress');
      if (progress) {
        if (percent === null) progress.removeAttribute('value');
        else progress.value = percent;
      }
    };
    xhr.upload.onprogress = (event) => {
      const percent = event.lengthComputable ? Math.min(100, Math.round(event.loaded * 100 / event.total)) : null;
      update(`${converting ? '上传中' : '上传识别中'}${percent !== null ? ` · ${percent}%` : '…'}`, percent);
    };
    xhr.upload.onload = () => update(converting ? '正在处理音频，请稍候…' : '正在自动识别…');
    xhr.onprogress = (event) => {
      if (!converting || xhr.status >= 400) return;
      if (retainedSize() + event.loaded > RESULT_BUDGET) {
        budgetExceeded = true;
        xhr.abort();
        return;
      }
      update(`正在接收结果${event.lengthComputable ? ` · ${Math.round(event.loaded * 100 / event.total)}%` : '…'}`,
        event.lengthComputable ? event.loaded * 100 / event.total : null);
    };
    xhr.onload = async () => {
      try {
        if (xhr.status < 200 || xhr.status >= 300) {
          let payload;
          try { payload = JSON.parse(await xhr.response.text()); } catch { /* generic error */ }
          throw fail(payload?.error?.code || 'server_error');
        }
        if (!converting) {
          const data = JSON.parse(await xhr.response.text());
          if (typeof data.can_convert !== 'boolean' || typeof data.state !== 'string') throw fail('server_error');
          resolve(data);
        } else {
          const disposition = xhr.getResponseHeader('Content-Disposition') || '';
          const filename = xhr.getResponseHeader('X-Audio-Filename');
          if (!xhr.response?.size || (!filename && !disposition.toLowerCase().startsWith('attachment'))) throw fail('server_error');
          if (retainedSize() + xhr.response.size > RESULT_BUDGET) throw fail('memory_limit');
          resolve({ blob: xhr.response, disposition, filename });
        }
      } catch (error) { reject(error); }
    };
    xhr.onerror = () => reject(fail('network'));
    xhr.ontimeout = () => reject(fail(converting ? 'processing_timeout' : 'upload_timeout'));
    xhr.onabort = () => reject(fail(budgetExceeded ? 'memory_limit' : 'aborted'));
    xhr.onloadend = () => { if (item.xhr === xhr) item.xhr = null; };
    xhr.send(item.file);
  });
}

async function inspectQueue() {
  if (inspecting || busy || !serviceReady) return;
  inspecting = true;
  try {
    let item;
    while ((item = items.find((entry) => entry.state === 'queued'))) {
      item.state = 'inspecting';
      item.message = '';
      renderItem(item);
      refresh();
      try {
        const info = await postFile(item, 'inspect', { filename: item.file.name });
        if (!isLive(item)) continue;
        item.info = info;
        item.state = info.can_convert ? 'ready' : 'blocked';
        item.message = info.can_convert ? '' : info.state === 'unsupported_format' && info.message ? info.message : messages[info.state] || '此类文件暂不可转换。';
      } catch (error) {
        if (!isLive(item)) continue;
        item.state = 'error';
        item.message = friendly(error);
      }
      if (isLive(item)) renderItem(item);
      refresh();
    }
  } finally {
    inspecting = false;
    refresh();
  }
}

function outputName(disposition, item, format, encodedName) {
  let name = '';
  if (encodedName) { try { name = decodeURIComponent(encodedName); } catch { /* fallback */ } }
  const encoded = /filename\*=UTF-8''([^;]+)/i.exec(disposition || '');
  if (!name && encoded) { try { name = decodeURIComponent(encoded[1]); } catch { /* fallback */ } }
  else if (!name) name = /filename="([^"]+)"/i.exec(disposition || '')?.[1] || '';
  name = name.replace(/[\\/\x00-\x1f]/g, '_').trim();
  if (!name) name = `${item.file.name.replace(/\.[^.]+$/, '')}.${format === 'source' ? 'audio' : format}`;
  return name;
}

async function startBatch() {
  if (busy || !serviceReady || items.some((item) => ['queued', 'inspecting'].includes(item.state))) return;
  const queue = readyItems().slice();
  if (!queue.length) return;
  if (retainedSize() >= RESULT_BUDGET) { notice('请先下载并移除已完成的文件，再继续转换。'); return; }
  const format = selectedFormat();
  const bitrate = $('bitrate').value;
  busy = true;
  stopRequested = false;
  batch = { total: queue.length, finished: 0, success: 0, failed: 0 };
  $('batch-progress').hidden = false;
  $('batch-meter').value = 0;
  $('batch-status').textContent = '开始转换';
  items.forEach(renderItem);
  refresh();
  try {
    for (const item of queue) {
      if (stopRequested) break;
      activeItem = item;
      item.state = 'converting';
      item.message = '准备上传…';
      item.percent = null;
      $('batch-status').textContent = '正在转换';
      $('batch-count').textContent = `${batch.finished + 1} / ${batch.total}`;
      renderItem(item);
      try {
        const params = { filename: item.file.name, format };
        if (bitrate) params.bitrate = bitrate;
        const result = await postFile(item, 'convert', params, true);
        item.result = { blob: result.blob, url: URL.createObjectURL(result.blob), name: outputName(result.disposition, item, format, result.filename), format };
        item.state = 'done';
        item.message = '';
        batch.success += 1;
      } catch (error) {
        if (error.code === 'aborted') {
          item.state = 'ready';
          item.message = '已停止，可重新转换';
          break;
        }
        item.state = 'error';
        item.message = friendly(error);
        batch.failed += 1;
      } finally { item.percent = null; }
      batch.finished += 1;
      $('batch-meter').value = batch.finished * 100 / batch.total;
      renderItem(item);
    }
  } finally {
    busy = false;
    activeItem = null;
    $('batch-status').textContent = stopRequested ? '已停止' : batch.failed ? `完成 · ${batch.success} 个成功` : '全部转换完成';
    $('batch-count').textContent = `${batch.finished} / ${batch.total}`;
    items.forEach(renderItem);
    refresh();
    if (batch.success) notice(`已完成 ${batch.success} 个文件，点击下载保存到本地。`);
  }
}

async function downloadAll() {
  if (busy || packing) return;
  const entries = items.filter((item) => item.result).map((item) => ({ name: item.result.name, blob: item.result.blob }));
  if (entries.length < 2) return;
  packing = true;
  refresh();
  try {
    const archive = await makeZip(entries);
    const url = URL.createObjectURL(archive);
    transientUrls.add(url);
    const anchor = element('a');
    anchor.href = url;
    anchor.download = '声转-转换结果.zip';
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
    setTimeout(() => { URL.revokeObjectURL(url); transientUrls.delete(url); }, 60000);
  } catch { notice('打包未完成，请使用各文件旁的下载按钮。'); }
  finally { packing = false; refresh(); }
}

$('choose-files').addEventListener('click', () => $('file-input').click());
$('file-input').addEventListener('change', (event) => { addFiles(event.target.files); event.target.value = ''; });
$('clear-files').addEventListener('click', () => {
  if (busy) return;
  for (const item of items.slice()) removeItem(item);
  $('batch-progress').hidden = true;
});
$('clear-completed').addEventListener('click', () => {
  if (busy) return;
  for (const item of items.filter((entry) => entry.result)) removeItem(item);
  if (!items.length) $('batch-progress').hidden = true;
});
$('file-search').addEventListener('input', filterFiles);
window.addEventListener('keydown', (event) => {
  if (event.key === '/' && !event.ctrlKey && !event.metaKey && !['INPUT', 'SELECT', 'TEXTAREA'].includes(document.activeElement?.tagName) && !$('help-dialog').open) {
    event.preventDefault();
    $('file-search').focus();
  }
});
document.querySelectorAll('.nav-item').forEach((link) => {
  link.addEventListener('click', () => {
    document.querySelectorAll('.nav-item').forEach((other) => {
      other.classList.toggle('active', other === link);
      if (other === link) other.setAttribute('aria-current', 'location');
      else other.removeAttribute('aria-current');
    });
  });
});
$('convert').addEventListener('click', startBatch);
$('stop').addEventListener('click', () => {
  stopRequested = true;
  $('batch-status').textContent = '正在停止…';
  activeItem?.xhr?.abort();
  refresh();
});
$('download-all').addEventListener('click', downloadAll);
$('reconnect').addEventListener('click', connect);
$('format-grid').addEventListener('change', updateFormats);
$('formats-help-top').addEventListener('click', () => $('help-dialog').showModal());
$('close-help').addEventListener('click', () => $('help-dialog').close());
$('help-done').addEventListener('click', () => $('help-dialog').close());
$('help-dialog').addEventListener('click', (event) => { if (event.target === $('help-dialog')) { const bounds = $('help-dialog').getBoundingClientRect(); if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) $('help-dialog').close(); } });
let dragDepth = 0;
window.addEventListener('dragover', (event) => { if (event.dataTransfer?.types.includes('Files')) event.preventDefault(); });
window.addEventListener('drop', (event) => { if (event.dataTransfer?.types.includes('Files')) event.preventDefault(); });
$('dropzone').addEventListener('dragenter', (event) => { event.preventDefault(); dragDepth += 1; if (!busy && serviceReady) $('dropzone').classList.add('dragging'); });
$('dropzone').addEventListener('dragover', (event) => { event.preventDefault(); if (event.dataTransfer) event.dataTransfer.dropEffect = busy || !serviceReady ? 'none' : 'copy'; });
$('dropzone').addEventListener('dragleave', () => { dragDepth = Math.max(0, dragDepth - 1); if (!dragDepth) $('dropzone').classList.remove('dragging'); });
$('dropzone').addEventListener('drop', (event) => { event.preventDefault(); dragDepth = 0; $('dropzone').classList.remove('dragging'); addFiles(event.dataTransfer?.files || []); });
window.addEventListener('beforeunload', (event) => { if (busy || items.some((item) => item.result)) event.preventDefault(); });
window.addEventListener('pagehide', (event) => {
  if (event.persisted) return; // Back-forward cache keeps the live page and its downloads.
  items.forEach((item) => { item.xhr?.abort(); disposeResult(item); });
  transientUrls.forEach((url) => URL.revokeObjectURL(url));
});
updateFormats();
connect();
