/**
 * Minimal shell for the desktop main-menu overlay view. The menu is
 * loaded into its own top-layer WebContentsView (desktop/main.js
 * openMainMenu), so it must NOT be wrapped by the app's (shell) chrome
 * (sidebar, tab strip). This route lives outside the (shell) group, so
 * it already skips that chrome — this layout only strips the page to a
 * transparent, non-scrolling stage for the floating panel.
 */
export default function MenuOverlayLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div
      style={{
        background: "transparent",
        width: "100vw",
        height: "100vh",
        overflow: "hidden",
        margin: 0,
      }}
    >
      {children}
    </div>
  );
}
