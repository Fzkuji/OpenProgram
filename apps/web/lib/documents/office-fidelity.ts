/** Whether the Office engine read a whole file.
 *
 *  The engine draws what it understood, and an export writes back exactly
 *  that. A presentation it reads with slides missing (a file another
 *  program wrote in a way it does not follow) would, once exported, replace
 *  the file with the slides it kept, though nobody deleted any. So before a
 *  writable editor may publish anything, the slides or sheets the file
 *  lists are counted and compared with an export the engine makes at once,
 *  before the person has touched it. */

/** The parts each format lists its units in, and how a unit is written there. */
const UNITS: Record<string, { part: string; unit: RegExp }> = {
  pptx: { part: "ppt/presentation.xml", unit: /<(?:\w+:)?sldId\b/g },
  pptm: { part: "ppt/presentation.xml", unit: /<(?:\w+:)?sldId\b/g },
  ppsx: { part: "ppt/presentation.xml", unit: /<(?:\w+:)?sldId\b/g },
  potx: { part: "ppt/presentation.xml", unit: /<(?:\w+:)?sldId\b/g },
  xlsx: { part: "xl/workbook.xml", unit: /<(?:\w+:)?sheet\b/g },
  xlsm: { part: "xl/workbook.xml", unit: /<(?:\w+:)?sheet\b/g },
  xltx: { part: "xl/workbook.xml", unit: /<(?:\w+:)?sheet\b/g },
};

export function fidelityCheckable(fileName: string): boolean {
  return Object.hasOwn(UNITS, extension(fileName));
}

function extension(fileName: string): string {
  return fileName.split(".").pop()?.toLowerCase() ?? "";
}

/** One stored part of a ZIP package, or null when it is not there. */
export async function readZipPart(bytes: Blob, name: string): Promise<Uint8Array | null> {
  const size = bytes.size;
  // The end-of-central-directory record sits in the last 64 KiB + 22 bytes.
  const tailStart = Math.max(0, size - 65_557);
  const tail = new DataView(await bytes.slice(tailStart).arrayBuffer());
  let end = -1;
  for (let i = tail.byteLength - 22; i >= 0; i--) {
    if (tail.getUint32(i, true) === 0x06054b50) { end = i; break; }
  }
  if (end < 0) return null;
  const entries = tail.getUint16(end + 10, true);
  const directorySize = tail.getUint32(end + 12, true);
  const directoryOffset = tail.getUint32(end + 16, true);
  const directory = new DataView(await bytes.slice(directoryOffset, directoryOffset + directorySize).arrayBuffer());
  const decoder = new TextDecoder();
  let at = 0;
  for (let n = 0; n < entries && at + 46 <= directory.byteLength; n++) {
    if (directory.getUint32(at, true) !== 0x02014b50) return null;
    const method = directory.getUint16(at + 10, true);
    const compressed = directory.getUint32(at + 20, true);
    const nameLength = directory.getUint16(at + 28, true);
    const extraLength = directory.getUint16(at + 30, true);
    const commentLength = directory.getUint16(at + 32, true);
    const local = directory.getUint32(at + 42, true);
    const entryName = decoder.decode(new Uint8Array(directory.buffer, directory.byteOffset + at + 46, nameLength));
    at += 46 + nameLength + extraLength + commentLength;
    if (entryName !== name) continue;
    const header = new DataView(await bytes.slice(local, local + 30).arrayBuffer());
    if (header.getUint32(0, true) !== 0x04034b50) return null;
    const start = local + 30 + header.getUint16(26, true) + header.getUint16(28, true);
    const data = bytes.slice(start, start + compressed);
    if (method === 0) return new Uint8Array(await data.arrayBuffer());
    if (method !== 8) return null;
    const inflated = data.stream().pipeThrough(new DecompressionStream("deflate-raw"));
    return new Uint8Array(await new Response(inflated).arrayBuffer());
  }
  return null;
}

/** How many slides or sheets the file lists, or null when it cannot be told. */
export async function countOfficeUnits(bytes: Blob, fileName: string): Promise<number | null> {
  const format = UNITS[extension(fileName)];
  if (!format) return null;
  try {
    const part = await readZipPart(bytes, format.part);
    if (!part) return null;
    return new TextDecoder().decode(part).match(format.unit)?.length ?? 0;
  } catch {
    return null;
  }
}

export interface IncompleteRead { expected: number; kept: number }

/** The engine's export kept fewer units than the file holds. */
export async function incompleteRead(source: Blob, exported: Blob, fileName: string): Promise<IncompleteRead | null> {
  const expected = await countOfficeUnits(source, fileName);
  if (!expected) return null;
  const kept = await countOfficeUnits(exported, fileName);
  if (kept === null || kept >= expected) return null;
  return { expected, kept };
}
