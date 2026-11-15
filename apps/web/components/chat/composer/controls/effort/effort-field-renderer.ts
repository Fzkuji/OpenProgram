import { effortFragmentShader, effortVertexShader } from "./effort-field-shaders";

export interface EffortRenderer { setActive: (active: boolean) => void; dispose: () => void }

/** Owns GPU resources and one animation clock, including context-loss recovery. */
export function createEffortRenderer(canvas: HTMLCanvasElement, onAvailable: (ready: boolean) => void): EffortRenderer {
  const doc = canvas.ownerDocument;
  const win = doc.defaultView!;
  let wanted = false, disposed = false;
  let applyActive: (() => void) | undefined;
  let release: (() => void) | undefined;

  function initialize() {
    const gl = canvas.getContext("webgl2", { alpha: true, antialias: false, depth: false, stencil: false, premultipliedAlpha: true });
    if (!gl) { onAvailable(false); return; }
    const shaders: WebGLShader[] = [];
    const program = gl.createProgram();
    if (!program) { onAvailable(false); return; }
    for (const [type, source] of [[gl.VERTEX_SHADER, effortVertexShader], [gl.FRAGMENT_SHADER, effortFragmentShader]] as const) {
      const shader = gl.createShader(type);
      if (!shader) { shaders.forEach(s => gl.deleteShader(s)); gl.deleteProgram(program); onAvailable(false); return; }
      gl.shaderSource(shader, source); gl.compileShader(shader); gl.attachShader(program, shader); shaders.push(shader);
    }
    gl.linkProgram(program);
    shaders.forEach(shader => gl.deleteShader(shader));
    const parallel = gl.getExtension("KHR_parallel_shader_compile");
    let compiling = 0;
    const finish = () => {
      if (disposed || gl.isContextLost()) return;
      if (parallel && !gl.getProgramParameter(program, parallel.COMPLETION_STATUS_KHR)) {
        compiling = win.requestAnimationFrame(finish); return;
      }
      if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
        gl.deleteProgram(program); release = undefined; onAvailable(false); return;
      }
      release = mount(gl, program);
    };
    release = () => { win.cancelAnimationFrame(compiling); gl.deleteProgram(program); };
    finish();
  }

  function mount(gl: WebGL2RenderingContext, program: WebGLProgram): () => void {
    gl.useProgram(program);
    const buffer = gl.createBuffer();
    if (!buffer) { gl.deleteProgram(program); onAvailable(false); return () => {}; }
    gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1,-1,1,-1,-1,1,-1,1,1,-1,1,1]), gl.STATIC_DRAW);
    const position = gl.getAttribLocation(program, "position");
    gl.enableVertexAttribArray(position); gl.vertexAttribPointer(position, 2, gl.FLOAT, false, 0, 0);
    const names = ["size","clock","seed","envelope","handle","starts[0]","origins[0]","strengths[0]","cold","hot","surfaceLeft","surfaceRight","blueTint","pinkTint","surface","inkOpacity"];
    const uniforms = Object.fromEntries(names.map(name => [name, gl.getUniformLocation(program, name)]));
    const starts = new Float32Array(8).fill(-1000), origins = new Float32Array(16), strengths = new Float32Array(8);
    let slot = 0, elapsed = 0, last = -1, fade = 0, held = 0, pressing = false;
    let lastPulse = -Infinity, lastPulseWall = -Infinity, interval = .45, raf = 0;
    let width = 1, height = 1;
    const motion = win.matchMedia("(prefers-reduced-motion: reduce)");
    const scheme = win.matchMedia("(prefers-color-scheme: dark)");
    const reduce = () => motion.matches || doc.documentElement.dataset.reduceMotion === "true";
    const uploadPulses = () => {
      gl.uniform1fv(uniforms["starts[0]"], starts);
      gl.uniform2fv(uniforms["origins[0]"], origins);
      gl.uniform1fv(uniforms["strengths[0]"], strengths);
    };
    function draw() {
      gl.viewport(0, 0, canvas.width, canvas.height);
      gl.uniform2f(uniforms.size, width, height);
      gl.uniform1f(uniforms.clock, elapsed);
      gl.uniform1f(uniforms.envelope, fade * fade * (3 - 2 * fade));
      gl.clear(gl.COLOR_BUFFER_BIT); gl.drawArrays(gl.TRIANGLES, 0, 6);
    }
    function resize() {
      const box = canvas.getBoundingClientRect();
      const w = Math.max(1, box.width), h = Math.max(1, box.height);
      for (let i = 0; i < 8; i++) { origins[2*i] *= w / width; origins[2*i+1] *= h / height; }
      width = w; height = h;
      const ratio = win.devicePixelRatio || 1;
      let pixelsW = Math.max(1, Math.round(w * ratio)), pixelsH = Math.max(1, Math.round(h * ratio));
      const cap = Math.min(1, Math.sqrt(4147200 / (pixelsW * pixelsH)));
      pixelsW = Math.max(1, Math.round(pixelsW * cap)); pixelsH = Math.max(1, Math.round(pixelsH * cap));
      if (canvas.width !== pixelsW || canvas.height !== pixelsH) { canvas.width = pixelsW; canvas.height = pixelsH; }
      uploadPulses(); draw();
    }
    function colors() {
      const mode = win.getComputedStyle(canvas).colorScheme;
      const dark = mode.includes("dark") || (!mode.includes("light") && scheme.matches);
      canvas.closest(".effort-field")?.setAttribute("data-mode", dark ? "dark" : "light");
      const rgb = (hex: string) => [1, 3, 5].map(i => parseInt(hex.slice(i, i+2), 16) / 255);
      gl.uniform3fv(uniforms.cold, dark ? [1,1,1] : rgb("#c6c0f3"));
      gl.uniform3fv(uniforms.hot, dark ? rgb("#a096eb") : rgb("#fbedf1"));
      gl.uniform4fv(uniforms.surfaceLeft, dark ? [.62,.5,.92,1] : [...rgb("#888edd"),1]);
      gl.uniform4fv(uniforms.surfaceRight, dark ? [.35,.25,.6,1] : [...rgb("#8964c4"),1]);
      gl.uniform4fv(uniforms.blueTint, dark ? [0,0,0,0] : [191/255,214/255,243/255,.28]);
      gl.uniform4fv(uniforms.pinkTint, dark ? [0,0,0,0] : [240/255,188/255,204/255,.28]);
      gl.uniform4fv(uniforms.surface, dark ? [0,.85,1.1,1] : [1,.92,.5,1.18]);
      gl.uniform2fv(uniforms.inkOpacity, dark ? [.08,.85] : [.28,1]);
    }
    function pulse(gain: number, x: number) {
      starts[slot] = elapsed; origins[slot*2] = x * width; origins[slot*2+1] = height * (.35 + Math.random() * .3);
      strengths[slot] = gain; slot = (slot + 1) % 8;
      lastPulse = elapsed; lastPulseWall = win.performance.now(); uploadPulses();
    }
    function frame(now: number) {
      raf = 0;
      const dt = last < 0 ? 0 : Math.max(0, (now - last) / 1000);
      last = now; elapsed += dt;
      fade = pressing ? Math.min(1, fade + dt / .7) : Math.max(0, fade - dt / .85);
      held = pressing ? held + dt : 0;
      if (pressing && elapsed - lastPulse > interval) {
        interval = .3 + Math.random() * .45;
        const gain = .85 * (.45 + .55 * Math.min(held / 1.6, 1));
        pulse(gain * (.85 + Math.random() * .3), 1 + (Math.random() - .5) * .08);
      }
      draw();
      if (!doc.hidden && (pressing || elapsed - lastPulse < 5 && fade > 0)) raf = win.requestAnimationFrame(frame);
    }
    const wake = () => { if (!raf && !doc.hidden) { last = -1; raf = win.requestAnimationFrame(frame); } };
    function sync() {
      colors();
      if (doc.hidden || reduce()) {
        pressing = false; fade = 0; held = 0; starts.fill(-1000); lastPulse = -Infinity;
        win.cancelAnimationFrame(raf); raf = 0; uploadPulses(); draw(); return;
      }
      if (wanted && !pressing) {
        if (win.performance.now() - lastPulseWall > 5000 && fade <= 0) {
          starts.fill(-1000); gl.uniform1f(uniforms.seed, Math.random() * 512);
        }
        pressing = true; held = 0; pulse(.85 * .45, 1);
      } else if (!wanted) pressing = false;
      if (pressing || fade > 0) wake(); else draw();
    }
    gl.clearColor(0,0,0,0); gl.uniform1f(uniforms.handle, 1); gl.uniform1f(uniforms.seed, 0);
    const resizeObserver = new ResizeObserver(resize); resizeObserver.observe(canvas);
    const themeObserver = new MutationObserver(sync);
    themeObserver.observe(doc.documentElement, { attributes: true, attributeFilter: ["data-theme","data-mode","data-reduce-motion","class","style"] });
    doc.addEventListener("visibilitychange", sync); motion.addEventListener("change", sync); scheme.addEventListener("change", sync);
    win.addEventListener("resize", resize);
    applyActive = sync; colors(); resize(); sync(); onAvailable(true);
    return () => {
      applyActive = undefined; win.cancelAnimationFrame(raf);
      resizeObserver.disconnect(); themeObserver.disconnect();
      doc.removeEventListener("visibilitychange", sync); motion.removeEventListener("change", sync); scheme.removeEventListener("change", sync);
      win.removeEventListener("resize", resize); gl.deleteBuffer(buffer); gl.deleteProgram(program);
    };
  }
  const lost = (event: Event) => { event.preventDefault(); release?.(); release = undefined; applyActive = undefined; onAvailable(false); };
  const restored = () => { if (!disposed) initialize(); };
  canvas.addEventListener("webglcontextlost", lost); canvas.addEventListener("webglcontextrestored", restored);
  initialize();
  return {
    setActive(active) { wanted = active; applyActive?.(); },
    dispose() { disposed = true; release?.(); canvas.removeEventListener("webglcontextlost", lost); canvas.removeEventListener("webglcontextrestored", restored); },
  };
}
