#!/usr/bin/env node

import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

export const FONT_ASSETS_DIR_ENV = 'ONLYOFFICE_BROWSER_FONT_ASSETS_DIR';
export const GENERATED_FONT_ASSETS_MANIFEST = 'onlyoffice-browser-font-assets.json';

const ZH_CORE_REQUIRED_FALLBACK_SOURCES = new Map([
  ['DejaVu Sans', /(?:^|\/)DejaVuSans(?:-Bold|-Oblique|-BoldOblique)?\.ttf$/i],
  ['OpenSymbol', /(?:^|\/)opens___\.ttf$/i],
  ['Symbola', /(?:^|\/)Symbola_hint\.ttf$/i],
]);

const ZH_CORE_OFFICE_GLYPH_RANGES = new Map([
  [0x21bb, 'DejaVu Sans'], // clockwise open circle arrow
  [0x2248, 'DejaVu Sans'], // almost equal
  [0x2726, 'DejaVu Sans'], // black four pointed star
]);

function usage() {
  return `Usage:
  npm run fonts:verify -- --input .onlyoffice-font-assets

Options:
  --input <dir>  Generated OnlyOffice font asset directory. Defaults to ${FONT_ASSETS_DIR_ENV}.
  --help         Show this help.`;
}

function readOption(argv, index, name) {
  const value = argv[index + 1];
  if (!value || value.startsWith('--')) {
    throw new Error(`Missing value for ${name}`);
  }
  return value;
}

export function parseVerifyFontAssetsArgs(argv, env = process.env) {
  const options = {
    input: env[FONT_ASSETS_DIR_ENV] || '',
    help: false,
  };

  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    switch (arg) {
      case '--input':
        options.input = readOption(argv, index, arg);
        index += 1;
        break;
      case '--help':
      case '-h':
        options.help = true;
        break;
      default:
        throw new Error(`Unknown option: ${arg}`);
    }
  }

  return options;
}

function assertFile(root, relativePath) {
  const filePath = path.resolve(root, relativePath);
  const relative = path.relative(root, filePath);
  if (!relative || relative.startsWith('..') || path.isAbsolute(relative)) {
    throw new Error(`Generated font asset path escapes input directory: ${relativePath}`);
  }
  if (!fs.existsSync(filePath) || !fs.statSync(filePath).isFile()) {
    throw new Error(`Generated font asset is missing: ${relativePath}`);
  }
}

function assertStringArray(value, label) {
  if (
    !Array.isArray(value) ||
    value.length === 0 ||
    !value.every((item) => typeof item === 'string' && item.length > 0)
  ) {
    throw new Error(`Generated font asset manifest has invalid ${label}`);
  }
}

function parseJsArray(source, name) {
  const match = source.match(new RegExp(`window\\["${name}"\\]\\s*=\\s*(\\[[\\s\\S]*?\\]);`));
  if (!match) {
    throw new Error(`Generated AllFonts.js is missing ${name}`);
  }

  try {
    return JSON.parse(match[1]);
  } catch (error) {
    throw new Error(`Generated AllFonts.js has invalid ${name}: ${error instanceof Error ? error.message : error}`);
  }
}

function assertInteger(value, label) {
  if (!Number.isInteger(value)) {
    throw new Error(`Generated AllFonts.js has invalid ${label}`);
  }
}

function isUrlLike(value) {
  return /^(?:[a-z][a-z\d+.-]*:)?\/\//i.test(value) || value.startsWith('data:') || value.startsWith('blob:');
}

function resolveGeneratedFontFile(root, fontFile) {
  if (typeof fontFile !== 'string' || fontFile.length === 0) {
    throw new Error('Generated AllFonts.js has an invalid font file entry');
  }
  if (isUrlLike(fontFile)) return null;

  const normalized = fontFile.replaceAll('\\', '/').replace(/^\/+/, '');
  const relativePath = normalized.startsWith('fonts/') ? normalized : `fonts/${normalized}`;
  const filePath = path.resolve(root, relativePath);
  const relative = path.relative(root, filePath);
  if (!relative || relative.startsWith('..') || path.isAbsolute(relative)) {
    throw new Error(`Generated AllFonts.js font file path escapes input directory: ${fontFile}`);
  }
  return { filePath, relativePath };
}

function assertGeneratedFontFile(root, fontFile) {
  const resolved = resolveGeneratedFontFile(root, fontFile);
  if (!resolved) return;
  if (!fs.existsSync(resolved.filePath) || !fs.statSync(resolved.filePath).isFile()) {
    throw new Error(`Generated AllFonts.js references missing font file: ${resolved.relativePath}`);
  }
}

function readPngDimensions(filePath) {
  const header = fs.readFileSync(filePath, { encoding: null, flag: 'r' }).subarray(0, 24);
  const pngSignature = '89504e470d0a1a0a';
  if (header.length < 24 || header.subarray(0, 8).toString('hex') !== pngSignature) {
    throw new Error(`Generated font thumbnail is not a PNG: ${path.basename(filePath)}`);
  }
  return {
    width: header.readUInt32BE(16),
    height: header.readUInt32BE(20),
  };
}

function isCjkCodePoint(value) {
  return (
    (value >= 0x2e80 && value <= 0x2eff) ||
    (value >= 0x3000 && value <= 0x303f) ||
    (value >= 0x3040 && value <= 0x30ff) ||
    (value >= 0x3100 && value <= 0x312f) ||
    (value >= 0x31a0 && value <= 0x31bf) ||
    (value >= 0x31f0 && value <= 0x31ff) ||
    (value >= 0x3400 && value <= 0x4dbf) ||
    (value >= 0x4e00 && value <= 0x9fff) ||
    (value >= 0xac00 && value <= 0xd7af) ||
    (value >= 0xf900 && value <= 0xfaff) ||
    (value >= 0x20000 && value <= 0x2fa1f)
  );
}

function isCjkFamilyName(name) {
  const lowered = name.toLowerCase();
  return [
    'fang',
    'hei',
    'heiti',
    'jheng',
    'kai',
    'kaiti',
    'ming',
    'mincho',
    'simsun',
    'song',
    'songti',
    'ukai',
    'wqy',
    'yahei',
    'deng',
    'gothic',
    'dotum',
    'gulim',
    'batang',
    'gungsuh',
    'noto sans cjk',
    'noto sans sc',
    'noto sans tc',
    'noto sans jp',
    'noto sans kr',
    'droid sans fallback',
    'pingfang',
    'hiragino sans gb',
    'arial unicode',
    '宋',
    '黑',
    '楷',
    '仿',
    '等线',
    '微软雅黑',
    '苹方',
    '蘋方',
  ].some((marker) => lowered.includes(marker));
}

function verifyAllFonts(root, allFontsRelativePath, thumbnails) {
  const allFontsPath = path.resolve(root, allFontsRelativePath);
  const source = fs.readFileSync(allFontsPath, 'utf8');
  const fontFiles = parseJsArray(source, '__fonts_files');
  const fontInfos = parseJsArray(source, '__fonts_infos');
  const fontRanges = parseJsArray(source, '__fonts_ranges');
  const visibleNames = source.includes('window["__fonts_visible_names"]')
    ? parseJsArray(source, '__fonts_visible_names')
    : [];

  if (!Array.isArray(fontFiles) || fontFiles.length === 0) {
    throw new Error('Generated AllFonts.js has invalid __fonts_files');
  }
  if (!Array.isArray(fontInfos) || fontInfos.length === 0) {
    throw new Error('Generated AllFonts.js has invalid __fonts_infos');
  }
  if (!Array.isArray(fontRanges) || fontRanges.length % 3 !== 0) {
    throw new Error('Generated AllFonts.js has invalid __fonts_ranges');
  }
  if (!Array.isArray(visibleNames) || !visibleNames.every((name) => typeof name === 'string' && name.length > 0)) {
    throw new Error('Generated AllFonts.js has invalid __fonts_visible_names');
  }

  for (const fontFile of fontFiles) assertGeneratedFontFile(root, fontFile);

  const fontInfoNames = new Set();
  for (const [infoIndex, info] of fontInfos.entries()) {
    if (!Array.isArray(info) || typeof info[0] !== 'string' || info[0].length === 0) {
      throw new Error(`Generated AllFonts.js has invalid __fonts_infos entry at index ${infoIndex}`);
    }
    fontInfoNames.add(info[0]);
    for (let slotIndex = 1; slotIndex < info.length; slotIndex += 2) {
      const fileIndex = info[slotIndex];
      const faceIndex = info[slotIndex + 1];
      assertInteger(fileIndex, `font file index for ${info[0]}`);
      assertInteger(faceIndex, `font face index for ${info[0]}`);
      if (fileIndex < -1 || fileIndex >= fontFiles.length) {
        throw new Error(`Generated AllFonts.js font ${info[0]} references missing __fonts_files index ${fileIndex}`);
      }
      if (faceIndex < -1) {
        throw new Error(`Generated AllFonts.js font ${info[0]} has invalid face index ${faceIndex}`);
      }
    }
  }

  for (const visibleName of visibleNames) {
    if (!fontInfoNames.has(visibleName)) {
      throw new Error(`Generated AllFonts.js visible font is missing from __fonts_infos: ${visibleName}`);
    }
  }

  for (let index = 0; index < fontRanges.length; index += 3) {
    const start = fontRanges[index];
    const end = fontRanges[index + 1];
    const fontInfoIndex = fontRanges[index + 2];
    assertInteger(start, `range start at __fonts_ranges[${index}]`);
    assertInteger(end, `range end at __fonts_ranges[${index + 1}]`);
    assertInteger(fontInfoIndex, `range font index at __fonts_ranges[${index + 2}]`);
    if (start > end || fontInfoIndex < 0 || fontInfoIndex >= fontInfos.length) {
      throw new Error(`Generated AllFonts.js has invalid __fonts_ranges entry at offset ${index}`);
    }
    if ((isCjkCodePoint(start) || isCjkCodePoint(end)) && !isCjkFamilyName(fontInfos[fontInfoIndex][0])) {
      throw new Error(
        `Generated AllFonts.js maps CJK range ${start}-${end} to non-CJK font ${fontInfos[fontInfoIndex][0]}`,
      );
    }
  }

  for (const thumbnail of thumbnails) {
    const thumbnailPath = path.resolve(root, thumbnail);
    const { height } = readPngDimensions(thumbnailPath);
    if (height % fontInfos.length !== 0) {
      throw new Error(
        `Generated font thumbnail row count does not match __fonts_infos: ${thumbnail} has height ${height} for ${fontInfos.length} fonts`,
      );
    }
    const rowHeight = height / fontInfos.length;
    if (rowHeight < 20) {
      throw new Error(
        `Generated font thumbnail row height is too small for __fonts_infos: ${thumbnail} has row height ${rowHeight}`,
      );
    }
  }
}

function verifyZhCoreFallbackChain(root, source, sourceMapRelativePath) {
  if (!sourceMapRelativePath) {
    throw new Error('Generated zh-core font assets require fontSourceMap');
  }

  const fontFiles = parseJsArray(source, '__fonts_files');
  const fontInfos = parseJsArray(source, '__fonts_infos');
  const fontRanges = parseJsArray(source, '__fonts_ranges');
  const sourceMap = JSON.parse(fs.readFileSync(path.resolve(root, sourceMapRelativePath), 'utf8'));
  const sourceByPackedFile = new Map(
    (sourceMap.fonts || []).map((entry) => [String(entry.file || '').replace(/^fonts\//, ''), String(entry.source || '')]),
  );

  for (const [familyName, expectedSource] of ZH_CORE_REQUIRED_FALLBACK_SOURCES) {
    const info = fontInfos.find((entry) => entry?.[0] === familyName);
    const packedFile = info?.[1] >= 0 ? fontFiles[info[1]] : '';
    const originalSource = sourceByPackedFile.get(packedFile) || '';
    if (!expectedSource.test(originalSource)) {
      throw new Error(`Generated zh-core fallback ${familyName} does not reference its real font source`);
    }
  }

  for (const [codePoint, expectedFamily] of ZH_CORE_OFFICE_GLYPH_RANGES) {
    let actualFamily = '';
    for (let index = 0; index < fontRanges.length; index += 3) {
      if (fontRanges[index] <= codePoint && codePoint <= fontRanges[index + 1]) {
        actualFamily = fontInfos[fontRanges[index + 2]]?.[0] || '';
        break;
      }
    }
    if (actualFamily !== expectedFamily) {
      throw new Error(
        `Generated zh-core Office glyph U+${codePoint.toString(16).toUpperCase()} maps to ${actualFamily || 'no font'} instead of ${expectedFamily}`,
      );
    }
  }
}

export function verifyOnlyOfficeFontAssets(input) {
  if (!input) {
    throw new Error(`Missing required --input <dir> or ${FONT_ASSETS_DIR_ENV}`);
  }

  const root = path.resolve(input);
  if (!fs.existsSync(root) || !fs.statSync(root).isDirectory()) {
    throw new Error(`Generated font asset directory does not exist: ${root}`);
  }

  const manifestPath = path.join(root, GENERATED_FONT_ASSETS_MANIFEST);
  if (!fs.existsSync(manifestPath) || !fs.statSync(manifestPath).isFile()) {
    throw new Error(`Generated font asset manifest is missing: ${GENERATED_FONT_ASSETS_MANIFEST}`);
  }

  const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
  if (!manifest || manifest.version !== 1) {
    throw new Error(`Generated font asset manifest has unsupported version: ${manifest?.version ?? 'missing'}`);
  }
  if (typeof manifest.allFonts !== 'string' || manifest.allFonts.length === 0) {
    throw new Error('Generated font asset manifest has invalid allFonts');
  }
  if (typeof manifest.fontSelection !== 'string' || manifest.fontSelection.length === 0) {
    throw new Error('Generated font asset manifest has invalid fontSelection');
  }
  if (
    manifest.fontSourceMap !== undefined &&
    (typeof manifest.fontSourceMap !== 'string' || manifest.fontSourceMap.length === 0)
  ) {
    throw new Error('Generated font asset manifest has invalid fontSourceMap');
  }
  assertStringArray(manifest.fontThumbnails, 'fontThumbnails');
  assertStringArray(manifest.fonts, 'fonts');

  assertFile(root, manifest.allFonts);
  assertFile(root, manifest.fontSelection);
  if (manifest.fontSourceMap) assertFile(root, manifest.fontSourceMap);
  for (const thumbnail of manifest.fontThumbnails) assertFile(root, thumbnail);
  for (const font of manifest.fonts) assertFile(root, font);
  verifyAllFonts(root, manifest.allFonts, manifest.fontThumbnails);
  if (manifest.fontSet === 'zh-core') {
    const source = fs.readFileSync(path.resolve(root, manifest.allFonts), 'utf8');
    verifyZhCoreFallbackChain(root, source, manifest.fontSourceMap);
  }

  return {
    root,
    fontSet: manifest.fontSet || 'unknown',
    fonts: manifest.fonts.length,
    thumbnails: manifest.fontThumbnails.length,
  };
}

function main() {
  const options = parseVerifyFontAssetsArgs(process.argv.slice(2));
  if (options.help) {
    console.log(usage());
    return;
  }
  const result = verifyOnlyOfficeFontAssets(options.input);
  console.log(
    `Verified OnlyOffice font assets: ${result.root} (${result.fontSet}, ${result.fonts} font files, ${result.thumbnails} thumbnails)`,
  );
}

if (import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    main();
  } catch (error) {
    console.error(error instanceof Error ? error.message : error);
    process.exitCode = 1;
  }
}
