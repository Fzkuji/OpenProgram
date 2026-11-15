// Kept external so redirects work with the worker's script-src 'self' policy.
(() => {
  const target = document.querySelector('link[rel="canonical"]').getAttribute('href');
  const [path, fragment] = target.split('#');
  location.replace(path + location.search + (location.hash || (fragment ? '#' + fragment : '')));
})();
