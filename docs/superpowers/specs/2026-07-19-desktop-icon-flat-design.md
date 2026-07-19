# Desktop App Icon Flat Design

## Scope

Update only the packaged desktop application icon. Keep the web favicon, README logo, sidebar wordmark, and loading indicators unchanged.

The maintained source remains `desktop/build/icon.svg`. The existing icon build script must regenerate `icon.png`, the ten `icon.iconset` representations, and `icon.icns` from that source.

## Approved Appearance

- Preserve the existing macOS squircle silhouette and transparent pixels outside it.
- Use a restrained deep-blue background: `#2C3C54` at the upper-left, `#202D43` through the middle, and `#101622` at the lower-right.
- Add only low-opacity light across the top and left portions of the background. Do not add a central glow, a concentrated corner glow, or a strong directional brightness change.
- Use a `268px` outer-ring radius and `44px` stroke. Keep the ring separate from every node; the blue node and ring inner edge retain roughly `21px` of clear space.
- Give the ring a five-stop cyan-blue-indigo-purple gradient and a visible but restrained lower-right underlay.
- Preserve the three node centers and radii: `(432, 432, 112)`, `(612, 572, 84)`, and `(456, 650, 44)`.
- Give every node a saturated multi-stop diagonal gradient, a lower-right underlay, and a short highlight arc that follows the same circle center exactly.
- Keep the style flat. Do not use spherical radial shading, strong bloom, large specular spots, or blurred node edges.

## Validation

- Run the maintained icon build and icon checks.
- Confirm every generated PNG size and the ICNS round trip.
- Inspect the icon at `1024px` and `128px`.
- Load the rebuilt icon in the packaged Electron application and verify its actual macOS Dock appearance.
