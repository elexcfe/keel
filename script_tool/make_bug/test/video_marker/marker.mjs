// Diagnostic prototype only. Not copied into tasks or used by production runners.
export function installProbe({ mode, token }) {
  if (mode === 'baseline') return;
  const probe = window.probe = { samples: [], running: true };
  let canvas, ctx;
  if (mode === 'marker') {
    canvas = document.createElement('canvas');
    canvas.width = 128;
    canvas.height = 64;
    canvas.style.cssText = 'position:fixed;left:16px;top:16px;width:128px;height:64px;z-index:2147483647;pointer-events:none;contain:strict';
    document.body.append(canvas);
    ctx = canvas.getContext('2d');
  }
  function crc16(bytes) {
    let crc = 0xffff;
    for (const byte of bytes) {
      crc ^= byte << 8;
      for (let bit = 0; bit < 8; bit++)
        crc = ((crc << 1) ^ ((crc & 0x8000) ? 0x1021 : 0)) & 0xffff;
    }
    return crc;
  }
  let seq = 0;
  function tick() {
    if (!probe.running) return;
    const start = performance.now();
    const row = {
      seq: ++seq, pageMs: start, trial: window.app.trial,
      present: !!document.querySelector('#chart'),
      scrollTop: document.querySelector('#sidebar').scrollTop,
    };
    if (ctx) {
      const bytes = new Uint8Array(16);
      const view = new DataView(bytes.buffer);
      view.setUint16(0, 0x4b45);
      view.setUint32(2, token);
      view.setUint16(6, 1); // Single document only in this experiment.
      view.setUint32(8, seq);
      view.setUint16(12, crc16(bytes.subarray(0, 12)));
      view.setUint16(14, 0xa55a);
      for (let bit = 0; bit < 128; bit++) {
        ctx.fillStyle = (bytes[bit >> 3] >> (7 - bit % 8)) & 1 ? '#fff' : '#000';
        ctx.fillRect((bit % 16) * 8, Math.floor(bit / 16) * 8, 8, 8);
      }
    }
    row.costMs = performance.now() - start;
    probe.samples.push(row);
    requestAnimationFrame(tick);
  }
  requestAnimationFrame(tick);
}
