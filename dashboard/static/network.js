/* Decorative neural field. Motion does not represent measured agent activity. */
(() => {
  "use strict";
  const canvas = document.getElementById("network");
  const container = document.getElementById("flow-canvas");
  const live = document.getElementById("live");
  const toggle = document.getElementById("motion-toggle");
  const context = canvas?.getContext("2d");
  if (!context || !container || !live) return;

  const reduced = matchMedia("(prefers-reduced-motion: reduce)");
  const fullCircle = Math.PI * 2;
  const storageKey = "hh-dashboard-motion";
  let enabled = true;
  try { enabled = localStorage.getItem(storageKey) !== "off"; } catch (_) { /* Optional preference. */ }
  let width = 0, height = 0, ratio = 1, nodes = [], links = [], neurons = [], edges = [];
  let meshColor = "#9567d4", particleColor = "#c392ff";
  let frameId = 0, previousFrame = 0, elapsed = 0, destroyed = false, inView = true;
  let pageVisible = true;
  const backdrop = document.createElement("canvas");
  const backdropContext = backdrop.getContext("2d");
  const seed = (value) => { const n = Math.sin(value * 127.1 + 311.7) * 43758.5453; return n - Math.floor(n); };
  const normalizedColor = (value, fallback) => {
    context.fillStyle = fallback;
    context.fillStyle = value || fallback;
    return context.fillStyle;
  };

  function buildBackdrop() {
    if (!backdropContext) return;
    backdrop.width = canvas.width;
    backdrop.height = canvas.height;
    const ctx = backdropContext;
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    const centerX = width * .5, centerY = nodes[2].y - 14;
    const radiusX = Math.min(width * .37, 360), radiusY = Math.min(height * .38, 122);
    ctx.strokeStyle = meshColor;
    ctx.lineWidth = .55;
    ctx.globalAlpha = .075;
    // An abstract ellipsoid, with no implied geographic locations or traffic.
    for (let i = -3; i <= 3; i++) {
      const latitude = i / 4;
      const breadth = Math.sqrt(1 - latitude * latitude);
      ctx.beginPath();
      ctx.ellipse(centerX, centerY + latitude * radiusY, radiusX * breadth, radiusY * .16 * breadth, -.08, 0, fullCircle);
      ctx.stroke();
    }
    for (let i = 1; i <= 5; i++) {
      ctx.beginPath();
      ctx.ellipse(centerX, centerY, radiusX * i / 5, radiusY, -.08, 0, fullCircle);
      ctx.stroke();
    }
    ctx.globalAlpha = 1;
  }

  function measure() {
    const bounds = canvas.getBoundingClientRect();
    if (!bounds.width || !bounds.height) return false;
    width = bounds.width;
    height = bounds.height;
    ratio = Math.min(devicePixelRatio || 1, 2);
    const bitmapWidth = Math.round(width * ratio), bitmapHeight = Math.round(height * ratio);
    if (canvas.width !== bitmapWidth || canvas.height !== bitmapHeight) {
      canvas.width = bitmapWidth;
      canvas.height = bitmapHeight;
    }
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    const rootStyle = getComputedStyle(document.documentElement);
    meshColor = normalizedColor(rootStyle.getPropertyValue("--network-mesh").trim(), "#9567d4");
    particleColor = normalizedColor(rootStyle.getPropertyValue("--network-particle").trim(), "#c392ff");
    nodes = [...document.querySelectorAll(".flow-node")].map((element) => {
      const orb = element.querySelector(".orb").getBoundingClientRect();
      const x = orb.left + orb.width / 2 - bounds.left;
      const y = orb.top + orb.height / 2 - bounds.top;
      const radius = orb.width / 2;
      const color = normalizedColor(getComputedStyle(element).getPropertyValue("--node").trim(), "#9448ff");
      const halo = context.createRadialGradient(x, y, radius * .65, x, y, radius * 2.8);
      halo.addColorStop(0, color);
      halo.addColorStop(1, "transparent");
      return {x, y, radius, color, halo};
    });
    if (nodes.length !== 5) return false;
    links = nodes.slice(0, -1).map((start, index) => {
      const end = nodes[index + 1];
      const gradient = context.createLinearGradient(start.x, start.y, end.x, end.y);
      gradient.addColorStop(0, start.color);
      gradient.addColorStop(1, end.color);
      return {start, end, gradient, index, amplitude: Math.min(25, (end.x - start.x) * .19)};
    });
    const fieldHeight = Math.min(height - 18, nodes[2].y + nodes[2].radius + 15);
    const quantity = width < 480 ? 36 : width < 800 ? 62 : 82;
    neurons = Array.from({length: quantity}, (_, index) => {
      const x = seed(index + 2) * width;
      const y = 16 + seed(index + 91) * (fieldHeight - 32);
      return {x, y, phase: seed(index + 117) * fullCircle, speed: .14 + seed(index + 67) * .14, radius: index % 9 === 0 ? 1.55 : .7, currentX: x, currentY: y};
    }).filter((neuron) => !nodes.some((node) => Math.hypot(neuron.x - node.x, neuron.y - node.y) < node.radius + 15));
    edges = [];
    const connectionDistance = Math.min(115, width * .21);
    for (let a = 0; a < neurons.length; a++) {
      const closest = [];
      for (let b = a + 1; b < neurons.length; b++) {
        const distance = Math.hypot(neurons[a].x - neurons[b].x, neurons[a].y - neurons[b].y);
        if (distance < connectionDistance) closest.push({a, b, distance});
      }
      edges.push(...closest.sort((a, b) => a.distance - b.distance).slice(0, 2));
    }
    buildBackdrop();
    return true;
  }

  function filamentPoint(link, progress, strand, time) {
    const envelope = Math.sin(progress * Math.PI);
    const wave = Math.sin(progress * Math.PI * 2 + strand * .83 - time * .48);
    return {
      x: link.start.x + (link.end.x - link.start.x) * progress,
      y: link.start.y + (link.end.y - link.start.y) * progress + envelope * wave * link.amplitude * (.45 + strand * .14),
    };
  }

  function drawField(time) {
    for (const neuron of neurons) {
      neuron.currentX = neuron.x + Math.cos(time * neuron.speed + neuron.phase) * 6;
      neuron.currentY = neuron.y + Math.sin(time * neuron.speed * .8 + neuron.phase) * 5;
    }
    context.strokeStyle = meshColor;
    context.lineWidth = .65;
    for (const edge of edges) {
      const a = neurons[edge.a], b = neurons[edge.b];
      context.globalAlpha = .09 + Math.sin(time * .35 + edge.a) * .035;
      context.beginPath();
      context.moveTo(a.currentX, a.currentY);
      context.lineTo(b.currentX, b.currentY);
      context.stroke();
    }
    context.fillStyle = particleColor;
    for (const [index, neuron] of neurons.entries()) {
      context.globalAlpha = .2 + (1 + Math.sin(time * .6 + neuron.phase)) * .18;
      context.beginPath();
      context.arc(neuron.currentX, neuron.currentY, neuron.radius, 0, fullCircle);
      context.fill();
      // A few impulses along the fine neural edges keep the background quiet.
      if (index % 12 !== 0 || !edges.length) continue;
      const edge = edges[(index * 3) % edges.length];
      const a = neurons[edge.a], b = neurons[edge.b];
      const progress = (time * .11 + neuron.phase / fullCircle) % 1;
      context.globalAlpha = Math.sin(progress * Math.PI) * .6;
      context.beginPath();
      context.arc(a.currentX + (b.currentX - a.currentX) * progress, a.currentY + (b.currentY - a.currentY) * progress, 1.2, 0, fullCircle);
      context.fill();
    }
  }

  function drawConnections(time) {
    for (const link of links) {
      context.strokeStyle = link.gradient;
      for (let strand = 0; strand < 5; strand++) {
        context.lineWidth = strand === 2 ? 1.2 : .7;
        context.globalAlpha = strand === 2 ? .27 : .12;
        context.beginPath();
        for (let step = 0; step <= 48; step++) {
          const point = filamentPoint(link, step / 48, strand, time);
          if (!step) context.moveTo(point.x, point.y); else context.lineTo(point.x, point.y);
        }
        context.stroke();
      }
      // Tapered pulses follow the actual curved filaments, with a soft light bloom.
      for (let pulse = 0; pulse < 2; pulse++) {
        const head = (time * (.21 + pulse * .025) + link.index * .2 + pulse * .53) % 1.25;
        const strand = pulse ? 4 : 1;
        context.shadowColor = link.end.color;
        context.shadowBlur = 9;
        for (let tail = 0; tail < 13; tail++) {
          const from = head - tail * .014;
          const to = from - .016;
          if (from <= 0 || from >= 1 || to <= 0) continue;
          const start = filamentPoint(link, from, strand, time);
          const end = filamentPoint(link, to, strand, time);
          context.globalAlpha = (1 - tail / 13) * .86;
          context.lineWidth = 1.9 - tail * .09;
          context.beginPath(); context.moveTo(start.x, start.y); context.lineTo(end.x, end.y); context.stroke();
        }
        if (head > 0 && head < 1) {
          const point = filamentPoint(link, head, strand, time);
          context.globalAlpha = .9;
          context.fillStyle = link.end.color;
          context.beginPath(); context.arc(point.x, point.y, 1.8, 0, fullCircle); context.fill();
        }
        context.shadowBlur = 0;
      }
    }
  }

  function drawCores(time) {
    nodes.forEach((node, index) => {
      const breathe = .5 + Math.sin(time * 1.1 - index * .8) * .5;
      context.fillStyle = node.halo;
      context.globalAlpha = .13 + breathe * .055;
      context.fillRect(node.x - node.radius * 2.8, node.y - node.radius * 2.8, node.radius * 5.6, node.radius * 5.6);
      context.strokeStyle = node.color;
      for (let orbit = 0; orbit < 2; orbit++) {
        const radius = node.radius + 8 + orbit * 7;
        const angle = time * (orbit ? -.3 : .38) + index * 1.5;
        context.lineWidth = orbit ? .65 : 1;
        context.globalAlpha = orbit ? .14 : .36;
        context.beginPath(); context.arc(node.x, node.y, radius, angle, angle + Math.PI * (orbit ? .63 : .85)); context.stroke();
        if (orbit) continue;
        context.fillStyle = node.color;
        context.globalAlpha = .78;
        context.shadowColor = node.color;
        context.shadowBlur = 6;
        context.beginPath(); context.arc(node.x + Math.cos(angle) * radius, node.y + Math.sin(angle) * radius, 1.55, 0, fullCircle); context.fill();
        context.shadowBlur = 0;
      }
    });
  }

  function draw(time) {
    context.clearRect(0, 0, width, height);
    if (!nodes.length || !width) return;
    context.save();
    context.beginPath();
    context.rect(0, 0, width, Math.min(height, nodes[2].y + nodes[2].radius + 21));
    context.clip();
    context.globalAlpha = 1;
    if (backdropContext) context.drawImage(backdrop, 0, 0, width, height);
    drawField(time);
    drawCores(time);
    drawConnections(time);
    context.restore();
  }

  function canAnimate() {
    return !destroyed && enabled && !reduced.matches && !document.hidden && pageVisible && !live.hidden && inView && width > 0;
  }

  function frame(timestamp) {
    frameId = 0;
    if (!canAnimate()) { reconcile(); return; }
    const delta = previousFrame ? timestamp - previousFrame : 34;
    if (delta >= 1000 / 30) {
      elapsed += Math.min(delta, 80) / 1000;
      previousFrame = timestamp;
      draw(elapsed);
    }
    frameId = requestAnimationFrame(frame);
  }

  function reconcile() {
    const active = canAnimate();
    document.body.classList.toggle("motion-active", active);
    if (toggle) {
      toggle.disabled = reduced.matches;
      toggle.setAttribute("aria-pressed", String(enabled && !reduced.matches));
      toggle.textContent = reduced.matches ? "Без анимации" : enabled ? "Анимация: вкл." : "Анимация: выкл.";
      toggle.title = reduced.matches ? "Анимация отключена настройкой уменьшения движения в системе." : "Декоративная анимация схемы. Не обозначает активность агента.";
    }
    if (active && !frameId) { previousFrame = 0; frameId = requestAnimationFrame(frame); }
    else if (!active && frameId) { cancelAnimationFrame(frameId); frameId = 0; previousFrame = 0; }
  }

  function refresh() {
    if (destroyed) return;
    if (!live.hidden && pageVisible && !document.hidden && measure()) draw(elapsed);
    reconcile();
  }
  function changeMotion() {
    enabled = !enabled;
    try { localStorage.setItem(storageKey, enabled ? "on" : "off"); } catch (_) { /* Optional preference. */ }
    reconcile();
  }
  function pageHide(event) {
    pageVisible = false;
    reconcile();
    context.clearRect(0, 0, width, height);
    if (!event.persisted) destroy();
  }
  function pageShow() { pageVisible = true; refresh(); }
  const resizeObserver = new ResizeObserver(refresh);
  resizeObserver.observe(container);
  const intersectionObserver = "IntersectionObserver" in window ? new IntersectionObserver(([entry]) => {
    inView = entry.isIntersecting;
    reconcile();
  }, {threshold: 0}) : null;
  intersectionObserver?.observe(container);
  toggle?.addEventListener("click", changeMotion);
  document.addEventListener("visibilitychange", refresh);
  reduced.addEventListener("change", refresh);
  window.addEventListener("pagehide", pageHide);
  window.addEventListener("pageshow", pageShow);

  function destroy() {
    if (destroyed) return;
    destroyed = true;
    reconcile();
    context.clearRect(0, 0, width, height);
    resizeObserver.disconnect();
    intersectionObserver?.disconnect();
    toggle?.removeEventListener("click", changeMotion);
    document.removeEventListener("visibilitychange", refresh);
    reduced.removeEventListener("change", refresh);
    window.removeEventListener("pagehide", pageHide);
    window.removeEventListener("pageshow", pageShow);
  }
  window.pipelineVisual = Object.freeze({refresh, destroy});
  document.fonts?.ready.then(refresh);
  refresh();
})();
