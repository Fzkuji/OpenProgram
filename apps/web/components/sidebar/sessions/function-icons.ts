import {
  AtomIcon,
  BotIcon,
  BoxIcon,
  ChartColumnIncreasingIcon,
  CompassIcon,
  CpuIcon,
  EarthIcon,
  EyeIcon,
  FeatherIcon,
  FingerprintIcon,
  FlameIcon,
  GaugeIcon,
  HammerIcon,
  KeyIcon,
  LanguagesIcon,
  MicIcon,
  PenToolIcon,
  RocketIcon,
  RouteIcon,
  SatelliteDishIcon,
  ScanTextIcon,
  ShieldCheckIcon,
  TelescopeIcon,
  TimerIcon,
} from "@/components/animated-icons";

/** The flat animated line icons (pqoqubbw set) a favorite program row
 *  shows. programs-meta stores the slug key. Deliberately DISJOINT from
 *  the glyphs the UI chrome already uses (sidebar nav, composer,
 *  cards) so a function icon never doubles as an interface control. */
export const FUNCTION_ICONS = {
  box: BoxIcon,
  bot: BotIcon,
  cpu: CpuIcon,
  atom: AtomIcon,
  compass: CompassIcon,
  telescope: TelescopeIcon,
  earth: EarthIcon,
  "satellite-dish": SatelliteDishIcon,
  eye: EyeIcon,
  "scan-text": ScanTextIcon,
  languages: LanguagesIcon,
  feather: FeatherIcon,
  "pen-tool": PenToolIcon,
  hammer: HammerIcon,
  key: KeyIcon,
  fingerprint: FingerprintIcon,
  "shield-check": ShieldCheckIcon,
  gauge: GaugeIcon,
  timer: TimerIcon,
  route: RouteIcon,
  mic: MicIcon,
  "chart-column": ChartColumnIncreasingIcon,
  flame: FlameIcon,
  rocket: RocketIcon,
} as const;

export type FunctionIconSlug = keyof typeof FUNCTION_ICONS;

export const DEFAULT_ICON: FunctionIconSlug = "box";

/** Resolve whatever programs-meta holds to a valid slug. The backend
 *  rewrites pre-flat-icon emoji values to slugs on read, so anything
 *  unrecognised here is genuinely unset. */
export function normalizeIcon(value: string | undefined | null): FunctionIconSlug {
  if (!value) return DEFAULT_ICON;
  if (value in FUNCTION_ICONS) return value as FunctionIconSlug;
  return DEFAULT_ICON;
}
