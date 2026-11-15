/** Pixel-space field rendering. All geometry is in CSS pixels, independent of DPR. */
export const effortVertexShader = `#version 300 es
in vec2 position;
uniform vec2 size;
out vec2 pixel;
void main() {
  pixel = (position + 1.0) * 0.5 * size;
  gl_Position = vec4(position, 0.0, 1.0);
}`;

export const effortFragmentShader = `#version 300 es
precision highp float;
in vec2 pixel;
out vec4 result;
uniform vec2 size;
uniform float clock;
uniform float seed;
uniform float envelope;
uniform float handle;
uniform float starts[8];
uniform vec2 origins[8];
uniform float strengths[8];
uniform vec3 cold;
uniform vec3 hot;
uniform vec4 surfaceLeft;
uniform vec4 surfaceRight;
uniform vec4 blueTint;
uniform vec4 pinkTint;
// surface: gap coverage, maximum opacity, horizontal exponent, energy multiplier.
uniform vec4 surface;
uniform vec2 inkOpacity;

float randomCell(vec2 p) {
  return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453);
}

void main() {
  float pitch = size.y / max(round(size.y / 4.0), 1.0);
  vec2 cell = floor(pixel / pitch);
  vec2 center = (cell + 0.5) * pitch;
  float scale = pitch / 4.0;
  vec2 edge = abs(pixel - center) - vec2(0.6 * scale);
  float distanceToShape = length(max(edge, 0.0)) + min(max(edge.x, edge.y), 0.0) - 0.9 * scale;
  float aa = min(fwidth(pixel.x), 1.5);
  float mask = 1.0 - smoothstep(-aa, aa, distanceToShape);
  if (mask <= 0.0 && surface.x <= 0.0) { result = vec4(0.0); return; }

  float arrivalOffset = (randomCell(cell + 41.7 + seed) - 0.5) * 0.2;
  float cellGain = 0.35 + 0.65 * randomCell(cell + 13.7 + seed);
  float sum = 0.0;
  float reached = 0.0;
  for (int pulse = 0; pulse < 8; ++pulse) {
    float age = clock - starts[pulse];
    if (age < 0.0 || age > 5.0) continue;
    float strength = clamp(strengths[pulse], 0.0, 1.0);
    vec2 delta = abs(cell - floor(origins[pulse] / pitch));
    float distance = delta.x + delta.y;
    float radius = mix(10.0, 55.0, strength);
    float attenuation = exp(-distance / (radius * 0.85));
    float outside = exp(-max(distance - radius, 0.0) * 0.12);
    float elapsed = age - distance / mix(20.0, 36.0, strength) - arrivalOffset;
    reached = max(reached, smoothstep(0.0, 0.15, elapsed) * outside);
    if (elapsed < 0.0) continue;
    if (randomCell(cell + 7.3 + seed + starts[pulse]) > mix(0.30, 0.85, strength)) continue;
    float flash = 1.25 * exp(-elapsed * 5.0);
    float tail = 0.65 * exp(-elapsed * mix(3.3, 0.77, strength));
    sum += (flash + tail) * mix(0.55, 1.0, strength) * attenuation * outside * cellGain;
  }

  float a = randomCell(cell + 8.8 + seed);
  float b = randomCell(cell + 88.8 + seed);
  float drift = 0.5 + 0.5 * sin(clock * 0.35 + a * 6.2832);
  float brightness = pow(mix(a, b, drift), 1.9);
  float modulation = 0.9 + 0.16 * sin(clock * 1.3 + cell.x * 0.45 + b * 2.0);
  float energy = clamp(sum, 0.0, 1.0) * envelope * mix(0.16, 1.0, brightness) * modulation * surface.w;
  float level = step(0.04, energy) + step(0.2, energy) + step(0.4, energy) + step(0.6, energy) + step(0.8, energy);
  float opacity = level == 0.0 ? 0.0 : mix(inkOpacity.x, inkOpacity.y, (level - 1.0) / 4.0);
  if (level == 5.0) {
    float period = 2.4 + 1.4 * randomCell(cell + 3.1 + seed);
    float phase = randomCell(cell + 5.5 + seed) * 6.2832;
    opacity *= 1.0 - 0.14 * (1.0 + sin(clock * 6.2832 / period + phase));
  }

  float along = clamp(pixel.x / max(handle * size.x, 1.0), 0.0, 1.0);
  vec4 ground = mix(surfaceLeft, surfaceRight, along);
  float groundAlpha = surface.y * pow(along, surface.z) * reached * envelope * mix(surface.x, 1.0, mask) * ground.a;
  vec3 color = mix(cold, hot, pow(level / 5.0, 1.4));
  float tint = randomCell(cell + 27.9 + seed);
  color = mix(color, blueTint.rgb, blueTint.a * smoothstep(0.62, 0.95, tint));
  color = mix(color, pinkTint.rgb, pinkTint.a * (1.0 - smoothstep(0.05, 0.38, tint)));
  float alpha = opacity * mask;
  result = vec4(color * alpha + ground.rgb * groundAlpha * (1.0 - alpha), alpha + groundAlpha * (1.0 - alpha));
}`;
