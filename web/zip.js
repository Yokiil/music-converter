// Store-mode ZIP keeps audio unchanged and avoids loading the whole archive
// into an ArrayBuffer. Names use ZIP's UTF-8 flag, including Chinese titles.
const table = Uint32Array.from({ length: 256 }, (_, index) => {
  let value = index;
  for (let bit = 0; bit < 8; bit += 1) value = (value & 1) ? 0xedb88320 ^ (value >>> 1) : value >>> 1;
  return value >>> 0;
});

async function checksum(blob) {
  let crc = 0xffffffff;
  for (let offset = 0; offset < blob.size; offset += 1024 * 1024) {
    const bytes = new Uint8Array(await blob.slice(offset, offset + 1024 * 1024).arrayBuffer());
    for (const byte of bytes) crc = table[(crc ^ byte) & 255] ^ (crc >>> 8);
  }
  return (crc ^ 0xffffffff) >>> 0;
}

function uniqueName(raw, seen) {
  const safe = raw.replace(/[\\/\x00-\x1f]/g, '_');
  const dot = safe.lastIndexOf('.');
  const stem = dot > 0 ? safe.slice(0, dot) : safe;
  const ext = dot > 0 ? safe.slice(dot) : '';
  let name = safe;
  let count = 1;
  while (seen.has(name.toLowerCase())) name = `${stem} (${count++})${ext}`;
  seen.add(name.toLowerCase());
  return name;
}

export async function makeZip(entries) {
  if (!entries.length || entries.length > 100) throw new Error('打包的文件数量无效。');
  const files = [];
  const directory = [];
  const seen = new Set();
  let offset = 0;
  let directorySize = 0;
  for (const entry of entries) {
    const name = new TextEncoder().encode(uniqueName(entry.name, seen));
    if (name.length > 65535 || offset + entry.blob.size > 0xffffffff) throw new Error('文件太大，无法打包。');
    const crc = await checksum(entry.blob);
    const local = new Uint8Array(30 + name.length);
    const lv = new DataView(local.buffer);
    lv.setUint32(0, 0x04034b50, true);
    lv.setUint16(4, 20, true);
    lv.setUint16(6, 0x800, true);
    lv.setUint16(12, 0x21, true); // 1980-01-01
    lv.setUint32(14, crc, true);
    lv.setUint32(18, entry.blob.size, true);
    lv.setUint32(22, entry.blob.size, true);
    lv.setUint16(26, name.length, true);
    local.set(name, 30);
    const central = new Uint8Array(46 + name.length);
    const cv = new DataView(central.buffer);
    cv.setUint32(0, 0x02014b50, true);
    cv.setUint16(4, 20, true);
    cv.setUint16(6, 20, true);
    cv.setUint16(8, 0x800, true);
    cv.setUint16(14, 0x21, true);
    cv.setUint32(16, crc, true);
    cv.setUint32(20, entry.blob.size, true);
    cv.setUint32(24, entry.blob.size, true);
    cv.setUint16(28, name.length, true);
    cv.setUint32(42, offset, true);
    central.set(name, 46);
    files.push(local, entry.blob);
    directory.push(central);
    offset += local.length + entry.blob.size;
    directorySize += central.length;
  }
  const end = new Uint8Array(22);
  const ev = new DataView(end.buffer);
  ev.setUint32(0, 0x06054b50, true);
  ev.setUint16(8, entries.length, true);
  ev.setUint16(10, entries.length, true);
  ev.setUint32(12, directorySize, true);
  ev.setUint32(16, offset, true);
  return new Blob([...files, ...directory, end], { type: 'application/zip' });
}
