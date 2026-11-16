// Opens the newest release's file for a fixed name such as
// latest.html?OpenProgram-mac-arm64.dmg. A release uploads each file once,
// under its versioned name (OpenProgram-1.3.0-mac-arm64.dmg, or with
// -unsigned), so the name is found in the newest release's file list. Anything
// else, or no answer, opens the newest release's page instead.
// Kept external so it runs under a script-src 'self' policy.
(() => {
  const repository = "Fzkuji/OpenProgram";
  const releasePage = `https://github.com/${repository}/releases/latest`;
  const names = [
    "OpenProgram-mac-arm64.dmg",
    "OpenProgram-mac-x64.dmg",
    "OpenProgram-win-x64.exe",
    "OpenProgram-win-arm64.exe",
    "OpenProgram-linux-amd64.deb",
    "OpenProgram-linux-x86_64.AppImage",
    "OpenProgram-linux-arm64.deb",
    "OpenProgram-linux-arm64.AppImage",
  ];
  const fallback = () => location.replace(releasePage);
  let wanted = "";
  try {
    wanted = decodeURIComponent((location.search || location.hash).slice(1));
  } catch (_error) {
    // A malformed name is just not one of ours.
  }
  if (!names.includes(wanted)) {
    fallback();
    return;
  }
  const rest = wanted.slice("OpenProgram-".length);
  const dot = rest.lastIndexOf(".");
  const stem = rest.slice(0, dot);
  const extension = rest.slice(dot + 1);
  const signed = new RegExp(`^OpenProgram-\\d+\\.\\d+\\.\\d+-${stem}\\.${extension}$`);
  const unsigned = new RegExp(`^OpenProgram-\\d+\\.\\d+\\.\\d+-${stem}-unsigned\\.${extension}$`);
  const prefix = `https://github.com/${repository}/releases/download/`;
  fetch(`https://api.github.com/repos/${repository}/releases/latest`, {
    headers: { Accept: "application/vnd.github+json" },
  })
    .then((response) => (response.ok ? response.json() : Promise.reject(new Error(String(response.status)))))
    .then((release) => {
      const assets = Array.isArray(release.assets) ? release.assets : [];
      const asset = assets.find((item) => signed.test(item.name)) || assets.find((item) => unsigned.test(item.name));
      const url = asset && String(asset.browser_download_url || "");
      if (url && url.startsWith(prefix)) location.replace(url);
      else fallback();
    })
    .catch(fallback);
})();
