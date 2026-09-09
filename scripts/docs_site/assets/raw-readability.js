/* Preserve table semantics and diagram text size inside narrow iframe views. */
(() => {
  // An unpainted iframe is transparent: a light-only document must not put
  // dark text on the surrounding site's dark canvas (or the reverse).
  const htmlStyle = getComputedStyle(document.documentElement);
  const bodyStyle = getComputedStyle(document.body);
  if (htmlStyle.backgroundColor === "rgba(0, 0, 0, 0)" &&
      bodyStyle.backgroundColor === "rgba(0, 0, 0, 0)") {
    const channels = bodyStyle.color.match(/[\d.]+/g).slice(0, 3)
      .map(value => Number(value) * (bodyStyle.color.startsWith("color(") ? 255 : 1));
    const brightness = channels.reduce((sum, value, i) => sum + value * [0.2126, 0.7152, 0.0722][i], 0);
    document.documentElement.style.backgroundColor = brightness < 140 ? "#ffffff" : "#171716";
  }
  function wrap(element, label) {
    const parent = element.parentElement;
    if (["auto", "scroll"].includes(getComputedStyle(parent).overflowX)) return;
    const region = document.createElement("div");
    region.className = "op-read-scroll";
    region.tabIndex = 0;
    region.setAttribute("role", "region");
    region.setAttribute("aria-label", label);
    element.before(region);
    region.append(element);
  }
  document.querySelectorAll("table").forEach(table => {
    const columns = Array.from(table.rows[0]?.cells || []).reduce((sum, cell) => sum + cell.colSpan, 0);
    if (columns > 2) table.style.minWidth = Math.max(parseFloat(getComputedStyle(table).minWidth) || 0, columns * 128) + "px";
    wrap(table, "Scrollable table");
  });
  document.querySelectorAll("svg").forEach(svg => {
    const box = svg.viewBox.baseVal;
    if ((box.width || svg.getBoundingClientRect().width) < 180) return;
    const sizes = Array.from(svg.querySelectorAll("text"))
      .map(text => parseFloat(getComputedStyle(text).fontSize)).filter(size => size >= 8);
    if (!sizes.length) return;
    wrap(svg, "Scrollable diagram");
    if (!box.width) return;
    const width = box.width * Math.min(1, 10 / Math.min(...sizes));
    svg.style.width = `max(100%, ${Math.ceil(width)}px)`;
    svg.style.maxWidth = "none";
    svg.style.height = "auto";
  });
})();
