export interface BookmarkNavigation {
  desktop: boolean;
  activeKind: string | undefined;
  splitAvailable: boolean;
  openSplit: (url: string) => void;
  openTab: (url: string) => void;
  collapseDock: () => void;
}

export function openBookmark(url: string, navigation: BookmarkNavigation): void {
  if (
    navigation.desktop &&
    navigation.activeKind === "session" &&
    navigation.splitAvailable
  ) {
    navigation.openSplit(url);
    navigation.collapseDock();
    return;
  }
  navigation.openTab(url);
}
