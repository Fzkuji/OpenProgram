declare module "utif" {
  export function decode(buffer: ArrayBuffer): Array<Record<string, unknown>>;
  export function decodeImage(buffer: ArrayBuffer, ifd: Record<string, unknown>): void;
  export function toRGBA8(ifd: Record<string, unknown>): ArrayLike<number>;
}
