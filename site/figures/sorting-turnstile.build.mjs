// mergeone: "one decision per merge, at swarm scale" as a sorting turnstile.
//
// Three feed chutes (the agent swarm) drop pucks (PRs) onto a single rotary
// gate. Each puck gets one decision: a three-lamp signal post lights green,
// amber or red; green pucks ride the single exit belt (main), amber ones are
// pushed onto the side shelf (human), red ones fall through the trap into
// the bin (block). A counter dial reads decisions per second.
//
//   node build.mjs            → merge-gate.html (dark)
//   node build.mjs --light    → merge-gate-light.html
//   node build.mjs --embed    → merge-gate.embed.html (figure + script only, for the landing page)
import { writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import * as k from "../../kit/iso-kit.mjs";
import { kitScript } from "../../scripts/inline-kit.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const theme = process.argv.includes("--light") ? "light" : "dark";
const embed = process.argv.includes("--embed");

const WIDTH = 640, HEIGHT = 400;

// ---------------------------------------------------------------- world (x right/along, y depth, z up)
const PLATE = { x: -150, y: -78, w: 300, d: 156, r: 8 };
const PLATE_H = 10, FOOT_H = 5;

const GATE = [-6, 0];           // turnstile hub
const GATE_R = 17;              // vane reach
const HUB_R = 5;
const DECK_Z = 14;              // top of the gate deck (pucks ride at this height)
const CHUTE_LEN = 70;           // feed chute length
const CHUTE_W = 16;
const CHUTE_RISE = 16;          // chutes slope down toward the gate
const CHUTES = [-46, 0, 46];    // chute centre y at the far end; they converge onto a tray before the gate
const TRAY = { x: GATE[0] - GATE_R - 28, y: -28, w: 24, d: 56, r: 2 }; // collecting tray in front of the gate
const CHUTE_X0 = TRAY.x;                 // chutes end at the tray's far edge

const BELT = { x: GATE[0] + GATE_R + 4, y: -8, w: 96, d: 16, r: 1.5 }; // exit belt (main)
const BELT_Z = DECK_Z - 2;
const SHELF = { x: GATE[0] - 10, y: 22, w: 46, d: 16, r: 2 };       // side shelf (human)
const SHELF_Z = DECK_Z - 1;
const BIN = { x: GATE[0] - 8, y: -GATE_R - 24, w: 16, d: 12, r: 1.5 };   // trap bin (block), in front of the deck, low
const BIN_H = 7;

const POST = [GATE[0] + GATE_R + 14, -30];  // signal post
const POST_TOP = 74;
const LAMP_Z = [40, 52, 64];                // red, amber, green bottom-to-top
const LAMP_R = 4.2;

const DIAL = [112, -52];
const DIAL_R = 14, DIAL_Z = DECK_Z + 2, DIAL_H = 7, DIAL_TOP = DIAL_Z + DIAL_H;
const KNOB = [112, 44];

const PUCK_R = 4, PUCK_H = 2.2;

const P = k.fitProjection(
  [...k.boxCorners(PLATE, -PLATE_H - FOOT_H, 0), [POST[0], POST[1], POST_TOP + 4], [CHUTE_X0 - CHUTE_LEN, CHUTES[0] - 8, DECK_Z + CHUTE_RISE + 10], [BELT.x + BELT.w, BELT.y + BELT.d, BELT_Z + 6], [BIN.x, BIN.y, 0]],
  WIDTH, HEIGHT, { pad: 28, azimuth: 52 },
);
const at = (p) => k.iso(p, P);
const S = (paths, style) => k.solidSvg(paths, style);
const L = (d, style) => k.lineSvg(d, style);
const D = (pts, style) => k.dotsSvg(pts, style);
const byDepth = (pts) => [...pts].sort((a, b) => k.depthOf([a[0], a[1], 0], P) - k.depthOf([b[0], b[1], 0], P));
// a single planar face in 3D as a kit solid (shade index: 0 lightest .. 3 darkest; "top" paints the lightest tone)
const face = (pts3, shade = null) => {
  const d = k.pathOf(pts3.map(at), true);
  const shades = [null, null, null, null];
  if (shade !== null) shades[shade] = d;
  return { fill: d, shades, top: shade === null ? d : "", outline: d };
};

// ---------------------------------------------------------------- base
function base() {
  const out = [`<path class="iso-halo" d="${k.haloOf(PLATE, -PLATE_H - FOOT_H, PLATE_H, P)}"/>`];
  for (const [x, y] of byDepth(k.corners(PLATE, 20))) {
    out.push(S(k.cylinder(x, y, 6.5, -PLATE_H - FOOT_H, 1.6, P, 28), { tone: "lo" }));
    out.push(S(k.cylinder(x, y, 5, -PLATE_H - FOOT_H + 1.6, FOOT_H - 1.6, P, 28), { tone: "mid" }));
  }
  out.push(S(k.slabOf(PLATE, -PLATE_H, PLATE_H, P, 8, 1.6), { tone: "lo" }));
  out.push(L(k.planOutline(k.insetPlan(PLATE, 5), 0, P), { tone: "faint" }));
  const screws = k.corners(PLATE, 10);
  out.push(L(screws.map(([x, y]) => k.ring(x, y, 2, 0, P, 16)).join(""), { tone: "lo" }));
  out.push(D(screws.map(([x, y]) => at([x, y, 0])), { size: 0.5 }));
  // label plate with three rule lines, front-left
  const label = { x: PLATE.x + 14, y: PLATE.y + PLATE.d - 24, w: 44, d: 14, r: 1.2 };
  out.push(S(k.slabOf(label, 0, 1, P, 3, 0.3), { tone: "mid", crease: "none" }));
  for (const dy of [4, 7, 10]) out.push(L(k.lineOnTop([label.x + 4, label.y + dy], [label.x + label.w - 4 - (dy === 10 ? 14 : 0), label.y + dy], 1, P), { tone: "lo" }));
  out.push(D([at([label.x + 2, label.y + 2, 1]), at([label.x + label.w - 2, label.y + 2, 1]), at([label.x + 2, label.y + label.d - 2, 1]), at([label.x + label.w - 2, label.y + label.d - 2, 1])], { size: 0.4 }));
  // ruler strip along the front edge
  const strip = { x: -60, y: PLATE.y + PLATE.d - 9, w: 200, d: 6, r: 1 };
  out.push(S(k.slabOf(strip, 0, 0.6, P, 3), { tone: "lo", crease: "none" }));
  const ticks = k.topTicks(strip.x + 4, strip.x + strip.w - 4, 4, 5, strip.y + 0.8, 0.6, [1.8, 3.4], P);
  out.push(L(ticks.minor, { tone: "lo" }), L(ticks.major, { tone: "mid" }));
  out.push(D([at([strip.x + 2, strip.y + 3, 0.6]), at([strip.x + strip.w - 2, strip.y + 3, 0.6])], { size: 0.45 }));
  return out.join("");
}

// ---------------------------------------------------------------- chutes (three feeds converging on the gate)
// Each chute is a straight channel from a far mouth (x = CHUTE_X0 - CHUTE_LEN, y = cy, high) to the gate edge (x = CHUTE_X0, y → 0, low).
function chutePath(cy) {
  const from = [CHUTE_X0 - CHUTE_LEN, cy, DECK_Z + CHUTE_RISE];
  const to = [CHUTE_X0, cy * 0.48, DECK_Z + 1];
  return { from, to };
}
function chute(cy, index) {
  const { from, to } = chutePath(cy);
  const out = [];
  // floor: a thin slab along the chute, drawn as an extruded quad in world space (4 corners)
  const dir = [to[0] - from[0], to[1] - from[1]];
  const len = Math.hypot(dir[0], dir[1]);
  const nx = -dir[1] / len, ny = dir[0] / len; // normal in plan
  const half = CHUTE_W / 2;
  const quad = (w0, w1, zOff) => [
    [from[0] + nx * w0, from[1] + ny * w0, from[2] + zOff], [to[0] + nx * w0, to[1] + ny * w0, to[2] + zOff],
    [to[0] + nx * w1, to[1] + ny * w1, to[2] + zOff], [from[0] + nx * w1, from[1] + ny * w1, from[2] + zOff],
  ];
  // support legs under the far end (two posts) and a mid post
  for (const t of [0.08, 0.5]) {
    const px = from[0] + dir[0] * t, py = from[1] + dir[1] * t, pz = from[2] + (to[2] - from[2]) * t;
    for (const side of [-half + 2.5, half - 2.5]) {
      out.push(S(k.cylinder(px + nx * side, py + ny * side, 1.6, 0, pz - 2, P, 14), { tone: "mid", crease: "none" }));
    }
  }
  // floor: the sloped top face, its near (+y side) edge face and its far-end lip, each a planar solid
  const topQ = quad(-half, half, 0), botQ = quad(-half, half, -2);
  out.push(S(face([botQ[0], botQ[1], topQ[1], topQ[0]], 3), { tone: "lo", crease: "none" }));        // -n side (back) edge face
  out.push(S(face(topQ), { tone: "mid", crease: "none" }));                                           // sloped top
  out.push(S(face([topQ[3], topQ[2], botQ[2], botQ[3]], 2), { tone: "mid", crease: "none" }));        // +n side (front) edge face
  out.push(S(face([topQ[1], topQ[2], botQ[2], botQ[1]], 2), { tone: "mid", crease: "none" }));        // gate-end lip
  // side walls: thin vertical plates along each edge, 5 high, with a top seam
  for (const side of [-half, half]) {
    const w0 = [from[0] + nx * side, from[1] + ny * side, from[2]], w1 = [to[0] + nx * side, to[1] + ny * side, to[2]];
    const t0 = [w0[0], w0[1], w0[2] + 5], t1 = [w1[0], w1[1], w1[2] + 5];
    const inner = side < 0 ? 1.2 : -1.2;
    const i0 = [w0[0] + nx * inner, w0[1] + ny * inner, w0[2]], i1 = [w1[0] + nx * inner, w1[1] + ny * inner, w1[2]];
    const it0 = [i0[0], i0[1], i0[2] + 5], it1 = [i1[0], i1[1], i1[2] + 5];
    if (side > 0) out.push(S(face([w0, w1, t1, t0], 2), { tone: "mid", crease: "none" }));           // outer face (+n faces viewer side)
    else out.push(S(face([i0, i1, it1, it0], 1), { tone: "mid", crease: "none" }));                   // inner face of the back wall
    out.push(S(face([t0, t1, it1, it0]), { tone: "mid", crease: "none" }));                           // wall top
    out.push(S(face([t1, it1, i1, w1], 2), { tone: "lo", crease: "none" }));                          // wall end at the gate
  }
  // entry funnel plate at the far mouth (a small vertical plate with a notch)
  out.push(S(k.slabOf({ x: from[0] - 3, y: from[1] - half - 1, w: 3, d: CHUTE_W + 2, r: 0.8 }, from[2] - 2, 9, P, 3, 0.4), { tone: "mid" }));
  out.push(D([at([from[0] - 1.5, from[1] - half + 1, from[2] + 6]), at([from[0] - 1.5, from[1] + half - 1, from[2] + 6])], { size: 0.4 }));
  // chute number as one engraved dot row on the funnel (1,2,3 dots): purposeful, not text
  out.push(D(Array.from({ length: index + 1 }, (_, i) => at([from[0] - 1.5, from[1] - 3 + i * 3, from[2] + 1])), { size: 0.35, tone: "hi" }));
  return out.join("");
}

// ---------------------------------------------------------------- gate deck, turnstile, exits
function deck() {
  const out = [];
  out.push(S(k.slabOf(TRAY, 0, DECK_Z, P, 4, 0.8), { tone: "mid" }));
  out.push(S(k.slabOf({ x: TRAY.x, y: TRAY.y - 1.5, w: TRAY.w, d: 1.5, r: 0.5 }, DECK_Z, 3.5, P, 2), { tone: "lo", crease: "none" }));
  out.push(S(k.slabOf({ x: TRAY.x, y: TRAY.y + TRAY.d, w: TRAY.w, d: 1.5, r: 0.5 }, DECK_Z, 3.5, P, 2), { tone: "lo", crease: "none" }));
  out.push(L(k.lineOnTop([TRAY.x + 2, TRAY.y + TRAY.d / 2], [TRAY.x + TRAY.w - 1, TRAY.y + TRAY.d / 2], DECK_Z + 0.2, P), { tone: "faint" }));
  // bridge plate from tray to gate deck
  out.push(S(k.slabOf({ x: TRAY.x + TRAY.w - 1, y: -9, w: GATE[0] - GATE_R - 8 - (TRAY.x + TRAY.w) + 2, d: 18, r: 1 }, 0, DECK_Z - 0.4, P, 3, 0.4), { tone: "mid", crease: "none" }));
  // belt drive motor at the far end of the belt
  const motor = [BELT.x + BELT.w + 2, BELT.y + BELT.d / 2];
  out.push(S(k.slabOf({ x: motor[0] - 2, y: motor[1] - 5, w: 6, d: 10, r: 1.5 }, 0, 5, P, 3, 0.5), { tone: "mid" }));
  out.push(S(k.cylinder(motor[0] + 2, motor[1], 4, 5, 7, P, 24, 0.5), { tone: "mid" }));
  out.push(L(k.knurl(motor[0] + 2, motor[1], 4, 9, 12, 22, P), { tone: "lo" }));
  out.push(D([at([motor[0] + 2, motor[1], 12])], { size: 0.45 }));
  // cable duct from signal post base to the gate deck, lying on the plate
  out.push(S(k.slabOf({ x: GATE[0] + GATE_R + 8, y: POST[1] - 1.5, w: POST[0] - 7 - (GATE[0] + GATE_R + 8), d: 3, r: 1 }, 0, 1.4, P, 3), { tone: "lo", crease: "none" }));
  out.push(S(k.slabOf({ x: GATE[0] + GATE_R + 6, y: POST[1] - 1.5, w: 3, d: 3, r: 0.8 }, 0, 2.2, P, 2), { tone: "mid", crease: "none" }));
  const deckPlan = { x: GATE[0] - GATE_R - 8, y: -GATE_R - 8, w: GATE_R * 2 + 16, d: GATE_R * 2 + 16, r: 6 };
  out.push(S(k.slabOf(deckPlan, 0, DECK_Z, P, 8, 1.2), { tone: "mid" }));
  out.push(L(k.ring(GATE[0], GATE[1], GATE_R + 3, DECK_Z, P, 48), { tone: "faint" }));
  // trap opening toward the bin (a dark inlay) and the bin itself below deck level, in front
  out.push(S(k.slabOf(BIN, 0, BIN_H, P, 4, 0.6), { tone: "lo" }));
  out.push(L(k.planOutline(k.insetPlan(BIN, 2), BIN_H, P, 4), { tone: "faint" }));
  out.push(S(k.slabOf({ x: GATE[0] - 5, y: -GATE_R - 7, w: 10, d: 7, r: 1 }, DECK_Z, 0.4, P, 3), { tone: "lo", crease: "none" }));
  // exit belt bed, two rollers, belt surface
  out.push(S(k.slabOf({ x: BELT.x - 2, y: BELT.y - 2, w: BELT.w + 4, d: BELT.d + 4, r: 2 }, 0, BELT_Z - 2, P, 6, 0.8), { tone: "mid" }));
  for (const x of [BELT.x + 4, BELT.x + BELT.w - 4]) {
    out.push(S(k.cylinder(x, BELT.y + BELT.d / 2, 3, BELT_Z - 3, 0, P, 20), { tone: "lo" })); // axle stub drawn as disc
  }
  out.push(S(k.slabOf(BELT, BELT_Z - 1, 1.2, P, 4), { tone: "hi", crease: "none" }));
  // belt lugs: a row of short cross lines on the belt (texture ending on the belt edges)
  for (let x = BELT.x + 8; x < BELT.x + BELT.w - 4; x += 8) out.push(L(k.lineOnTop([x, BELT.y + 1], [x, BELT.y + BELT.d - 1], BELT_Z + 0.2, P), { tone: "lo" }));
  // side shelf (human) with a low fence
  out.push(S(k.slabOf(SHELF, 0, SHELF_Z, P, 4, 0.8), { tone: "mid" }));
  out.push(S(k.slabOf({ x: SHELF.x, y: SHELF.y + SHELF.d - 1.5, w: SHELF.w, d: 1.5, r: 0.5 }, SHELF_Z, 3, P, 2), { tone: "lo", crease: "none" }));
  out.push(D([at([SHELF.x + 3, SHELF.y + 3, SHELF_Z]), at([SHELF.x + SHELF.w - 3, SHELF.y + 3, SHELF_Z])], { size: 0.4 }));
  return out.join("");
}

function turnstileHub() {
  const out = [];
  out.push(S(k.cylinder(GATE[0], GATE[1], HUB_R + 2, DECK_Z, 1.2, P, 32), { tone: "lo" }));
  out.push(S(k.cylinder(GATE[0], GATE[1], HUB_R, DECK_Z + 1.2, 6, P, 32, 0.4), { tone: "mid" }));
  out.push(L(k.knurl(GATE[0], GATE[1], HUB_R, DECK_Z + 4.2, DECK_Z + 7.2, 30, P), { tone: "lo" }));
  out.push(S(k.cylinder(GATE[0], GATE[1], 1.6, DECK_Z + 7.2, 3, P, 16), { tone: "hi" }));
  return out.join("");
}
// vanes are built once at angle 0 and rotated in the page by re-writing the path (shape changes per frame)
function vanesAt(theta) {
  const paths = [];
  for (let i = 0; i < 3; i++) {
    const a = theta + (i * Math.PI * 2) / 3;
    const ux = Math.cos(a), uy = Math.sin(a), nx = -uy * 0.9, ny = ux * 0.9;
    const r0 = HUB_R + 0.5, r1 = GATE_R;
    const z0 = DECK_Z + 1.2, z1 = DECK_Z + 7;
    const q = (r, s, z) => [GATE[0] + ux * r + nx * s, GATE[1] + uy * r + ny * s, z];
    const faceA = [q(r0, -1, z0), q(r1, -1, z0), q(r1, -1, z1), q(r0, -1, z1)];
    const faceB = [q(r0, 1, z0), q(r1, 1, z0), q(r1, 1, z1), q(r0, 1, z1)];
    const top = [q(r0, -1, z1), q(r1, -1, z1), q(r1, 1, z1), q(r0, 1, z1)];
    const end = [q(r1, -1, z0), q(r1, 1, z0), q(r1, 1, z1), q(r1, -1, z1)];
    const depth = k.depthOf(q((r0 + r1) / 2, 0, (z0 + z1) / 2), P);
    paths.push({ depth, faces: [faceA, faceB, end, top] });
  }
  paths.sort((a, b) => a.depth - b.depth);
  return paths.map(({ faces }) => faces.map((f, i) => i === 3
    ? `<path class="iso-top" d="${k.pathOf(f.map(at), true)}"/><path class="iso-line iso-edge" data-tone="hi" d="${k.pathOf(f.map(at), true)}"/>`
    : `<path class="iso-fill" d="${k.pathOf(f.map(at), true)}"/><path class="iso-shade" data-shade="${[3, 1, 2][i]}" d="${k.pathOf(f.map(at), true)}"/>`).join("")).join("");
}

function signalPost() {
  const out = [];
  const [x, y] = POST;
  out.push(S(k.slabOf({ x: x - 7, y: y - 7, w: 14, d: 14, r: 2.5 }, 0, 4, P, 4, 0.6), { tone: "mid" }));
  out.push(D(k.corners({ x: x - 7, y: y - 7, w: 14, d: 14 }, 2.5).map(([cx, cy]) => at([cx, cy, 4])), { size: 0.4 }));
  out.push(S(k.cylinder(x, y, 2.4, 4, LAMP_Z[0] - LAMP_R - 4, P, 20), { tone: "mid", crease: "none" }));
  out.push(L([16, 28].map((z) => k.sideArc(x, y, 2.4, z, P, 12)).join(""), { tone: "faint" }));
  // lamp housing: a vertical box with three round lenses facing the viewer-ish (+y face)
  const house = { x: x - 5, y: y - 4, w: 10, d: 8, r: 1.5 };
  out.push(S(k.slabOf(house, LAMP_Z[0] - LAMP_R - 2, LAMP_Z[2] + LAMP_R + 2 - (LAMP_Z[0] - LAMP_R - 2), P, 4, 0.6), { tone: "mid" }));
  out.push(S(k.cylinder(x, y, 2.4, LAMP_Z[2] + LAMP_R + 2, 2, P, 20), { tone: "lo" }));
  out.push(S(k.cylinder(x, y, 3.4, LAMP_Z[2] + LAMP_R + 4, 1.2, P, 20), { tone: "mid" }));
  return out.join("");
}
// a lens is a disc on the +y face of the housing; drawn via sideMatrix so it sits on the face
function lens(index) {
  const [x, y] = POST;
  const z = LAMP_Z[index];
  const face = k.sideMatrix([x, y + 4.01], z, P, "y");
  const colour = ["red", "amber", "green"][index];
  return `<g transform="${face}"><circle data-halo="${colour}" class="iso-lamp" r="${LAMP_R + 2.2}" fill="currentColor" opacity="0" style="filter:blur(1.6px)"/><circle class="iso-fill" r="${LAMP_R}"/><circle data-lamp="${colour}" class="iso-lamp" r="${LAMP_R - 1.1}" fill="currentColor" opacity="0.18"/><circle class="iso-line" data-tone="faint" r="${LAMP_R - 1.1}" fill="none"/></g>`;
}

function counterDial() {
  const out = [];
  const [x, y] = DIAL;
  out.push(S(k.slabOf({ x: x - 9, y: y - 9, w: 18, d: 18, r: 3 }, 0, DIAL_Z, P, 4, 0.8), { tone: "mid" }));
  out.push(S(k.cylinder(x, y, DIAL_R, DIAL_Z, DIAL_H, P, 56, 0.8), { tone: "hi", crease: "lo" }));
  out.push(L(k.knurl(x, y, DIAL_R, DIAL_TOP - 2.6, DIAL_TOP, 64, P, 0.3), { tone: "lo" }));
  out.push(L(k.ring(x, y, DIAL_R - 2, DIAL_TOP, P, 56), { tone: "lo" }));
  const ticks = k.radialTicks(x, y, DIAL_R - 2.6, 40, 5, DIAL_TOP, [1.1, 2.4], P);
  out.push(L(ticks.minor, { tone: "lo" }), L(ticks.major, { tone: "mid" }));
  out.push(L(k.ring(x, y, 1.4, DIAL_TOP, P, 14), { tone: "mid" }));
  out.push(k.faceTextSvg(k.topMatrix([x, y + 7], DIAL_TOP, P, "x"), "dec/s", { size: 2.2, tone: "lo", anchor: "middle" }));
  return out.join("");
}
const needleAt = (turn) => {
  const a = turn * Math.PI * 2;
  const [x, y] = DIAL;
  return k.lineOnTop([x - Math.sin(a) * 2.6, y + Math.cos(a) * 2.6], [x + Math.sin(a) * (DIAL_R - 3.6), y - Math.cos(a) * (DIAL_R - 3.6)], DIAL_TOP + 0.2, P);
};

function speedKnob() {
  const out = [];
  const [x, y] = KNOB;
  out.push(S(k.slabOf({ x: x - 7, y: y - 7, w: 14, d: 14, r: 2 }, 0, 3, P, 4, 0.5), { tone: "mid" }));
  out.push(S(k.cylinder(x, y, 5.5, 3, 5, P, 32), { tone: "mid" }));
  out.push(L(k.knurl(x, y, 5.5, 3, 8, 34, P), { tone: "lo" }));
  out.push(L(k.lineOnTop([x, y], [x + 5.5, y], 8, P), { tone: "hi" }));
  out.push(D([at([x, y, 8])], { size: 0.5, tone: "hi" }));
  const thumb = [x - 5, y + 5];
  out.push(S(k.cylinder(thumb[0], thumb[1], 1.6, 3, 3.5, P, 16), { tone: "mid" }));
  out.push(L(k.knurl(thumb[0], thumb[1], 1.6, 3, 6.5, 12, P), { tone: "lo" }));
  return out.join("");
}

// puck: a short cylinder; the one in the gate is lit
const puckSvg = (lit) => S(k.cylinder(0, 0, PUCK_R, 0, PUCK_H, P, 24, 0.4), { tone: "hi", lit });

// ---------------------------------------------------------------- assemble
const svg = k.figureSvg({
  width: WIDTH, height: HEIGHT,
  label: "A sorting turnstile. Three feed chutes drop pucks onto one rotary gate. Each puck gets a single decision shown on a three-lamp signal post: green rides the exit belt, amber is pushed onto the side shelf, red drops through the trap into the bin. A counter dial reads decisions per second.",
  body: [
    base(),
    chute(CHUTES[0], 0), chute(CHUTES[1], 1), chute(CHUTES[2], 2),
    deck(),
    `<g data-part="pucks-back"></g>`,
    turnstileHub(),
    `<g data-part="vanes" class="iso-solid">${vanesAt(0)}</g>`,
    `<g data-part="pucks-front"></g>`,
    signalPost(), lens(0), lens(1), lens(2),
    counterDial(),
    `<path data-part="needle" class="iso-line" data-tone="lit" data-free="start" d="${needleAt(0.12)}" style="stroke-width:1"/>`,
    speedKnob(),
  ],
});

const live = `
const P = ${JSON.stringify(P)};
const GATE = ${JSON.stringify(GATE)}, GATE_R = ${GATE_R}, HUB_R = ${HUB_R}, DECK_Z = ${DECK_Z}, CHUTE_RISE = ${CHUTE_RISE}, CHUTE_LEN = ${CHUTE_LEN}, CHUTE_X0 = ${CHUTE_X0};
const CHUTES = ${JSON.stringify(CHUTES)}, TRAY = ${JSON.stringify(TRAY)}, BELT = ${JSON.stringify(BELT)}, BELT_Z = ${BELT_Z}, SHELF = ${JSON.stringify(SHELF)}, SHELF_Z = ${SHELF_Z}, BIN = ${JSON.stringify(BIN)}, BIN_H = ${BIN_H};
const DIAL = ${JSON.stringify(DIAL)}, DIAL_R = ${DIAL_R}, DIAL_TOP = ${DIAL_TOP}, PUCK_R = ${PUCK_R}, PUCK_H = ${PUCK_H};
const PUCK_SVG = ${JSON.stringify(puckSvg(false))}, PUCK_LIT = ${JSON.stringify(puckSvg(true))};
const stage = document.querySelector(".iso-stage"), svg = stage.querySelector("svg");
const vanes = svg.querySelector('[data-part="vanes"]'), back = svg.querySelector('[data-part="pucks-back"]'), front = svg.querySelector('[data-part="pucks-front"]');
const needle = svg.querySelector('[data-part="needle"]'), readout = document.querySelector("[data-readout]");
const lamps = { red: svg.querySelector('[data-lamp="red"]'), amber: svg.querySelector('[data-lamp="amber"]'), green: svg.querySelector('[data-lamp="green"]') };
const halos = { red: svg.querySelector('[data-halo="red"]'), amber: svg.querySelector('[data-halo="amber"]'), green: svg.querySelector('[data-halo="green"]') };
const still = matchMedia("(prefers-reduced-motion: reduce)");
const at = (p) => iso(p, P);
const vanesAt = ${vanesAt.toString().replaceAll("k.", "")};
const needleAt = ${needleAt.toString().replaceAll("k.", "")};
// --- model: pucks arrive on three chutes, queue at the gate, each gets a verdict, then travels its exit.
// verdict mix from gatebench run 2 replay (jev, default policy): pass 28%, hold 59%, block 13%
const MIX = [["green", 0.28], ["amber", 0.59], ["red", 0.13]];
let rate = 1.4;               // pucks per second at the gate (knob: 0.6 .. 3.2)
let theta = 0, thetaTarget = 0, pucks = [], clock = 0, lastSpawn = 0, decided = 0, window = [], seed = 7;
let frame = 0, last = 0, visible = false, lamp = null, lampT = 0, touring = true, idleTimer = 0;
const rnd = () => (seed = (seed * 1664525 + 1013904223) % 4294967296) / 4294967296;
const verdictOf = () => { const r = rnd(); let acc = 0; for (const [c, p] of MIX) { acc += p; if (r < acc) return c; } return "amber"; };
function chuteStart(i) { const cy = CHUTES[i]; return { from: [CHUTE_X0 - CHUTE_LEN + 6, cy, DECK_Z + CHUTE_RISE], to: [CHUTE_X0, cy * 0.18, DECK_Z] }; }
function posOf(p) {
  // phases: 0 chute (t 0..1), 1 waiting at gate edge, 2 in gate (riding a vane), 3 exit (belt / shelf / bin), 4 gone
  if (p.phase === 0) { const { from, to } = chuteStart(p.chute); const t = p.t; return [from[0] + (to[0] - from[0]) * t, from[1] + (to[1] - from[1]) * t, from[2] + (to[2] - from[2]) * t]; }
  if (p.phase === 1) { const q = Math.min(3, p.queue || 0); return [TRAY.x + 4 + q * 5.2, TRAY.y + TRAY.d / 2 + (p.chute - 1) * 9, DECK_Z]; }
  if (p.phase === 2) { const a = theta + p.vane * Math.PI * 2 / 3 + 0.55; const r = HUB_R + 7; return [GATE[0] + Math.cos(a) * r, GATE[1] + Math.sin(a) * r, DECK_Z + 1.2]; }
  if (p.phase === 3) {
    const t = p.t;
    if (p.verdict === "green") return [BELT.x + 2 + t * (BELT.w - 6), BELT.y + BELT.d / 2, BELT_Z + 0.2];
    if (p.verdict === "amber") return [GATE[0] + 2 + t * 18, GATE[1] + GATE_R - 2 + t * (SHELF.y + 7 - GATE_R), SHELF_Z + (t < 0.25 ? DECK_Z - SHELF_Z : 0) * (1 - t / 0.25 > 0 ? 1 - t / 0.25 : 0)];
    return [GATE[0], GATE[1] - GATE_R + 2 - t * 6, DECK_Z + 1.2 - t * (DECK_Z + 1.2 - BIN_H + 1)];
  }
  return null;
}
function draw() {
  vanes.innerHTML = vanesAt(theta);
  needle.setAttribute("d", needleAt(Math.min(0.92, 0.04 + (window.length / 6) * 0.3)));
  const html = { back: [], front: [] };
  const gateDepth = depthOf([GATE[0], GATE[1], DECK_Z], P);
  for (const p of pucks) {
    const pos = posOf(p); if (!pos) continue;
    const lit = p.phase === 2;
    const item = '<g transform="' + translateAlong(P, pos) + '">' + (lit ? PUCK_LIT : PUCK_SVG) + '</g>';
    (depthOf(pos, P) < gateDepth - 2 ? html.back : html.front).push([depthOf(pos, P), item]);
  }
  back.innerHTML = html.back.sort((a, b) => a[0] - b[0]).map((x) => x[1]).join("");
  front.innerHTML = html.front.sort((a, b) => a[0] - b[0]).map((x) => x[1]).join("");
  for (const [name, el] of Object.entries(lamps)) { const on = name === lamp ? Math.max(0, 1 - lampT / 1.1) : 0; el.setAttribute("opacity", String(0.18 + 0.8 * on)); halos[name].setAttribute("opacity", String(0.55 * on)); }
  const perS = window.length / 6;
  const text = rate.toFixed(1) + " PR/s in · " + perS.toFixed(1) + " decisions/s · " + decided + " decided";
  if (readout.textContent !== text) readout.textContent = text;
}
function tick(now) {
  const dt = last ? Math.min((now - last) / 1000, 1 / 30) : 1 / 60; last = now;
  clock += dt;
  if (still.matches) { draw(); frame = 0; return; }
  // spawn
  if (clock - lastSpawn > 1 / rate) { lastSpawn = clock; pucks.push({ chute: Math.floor(rnd() * 3), phase: 0, t: 0, verdict: null, vane: 0, born: clock }); }
  // gate: one puck at a time; a waiting puck boards when the gate is free, gate turns 1/3 per puck
  const inGate = pucks.find((p) => p.phase === 2);
  if (!inGate) {
    const waiting = pucks.filter((p) => p.phase === 1).sort((a, b) => a.born - b.born)[0];
    if (waiting) { waiting.phase = 2; waiting.t = 0; waiting.verdict = verdictOf(); waiting.vane = 0; thetaTarget += Math.PI * 2 / 3; lamp = waiting.verdict; lampT = 0; }
  }
  lampT += dt;
  theta += (thetaTarget - theta) * (1 - Math.exp(-dt / 0.22));
  for (const p of pucks) {
    if (p.phase === 0) { p.t += dt * 0.55; if (p.t >= 1) { p.t = 1; p.phase = 1; p.queue = pucks.filter((q) => q.phase === 1).length; } }
    else if (p.phase === 2) { if (Math.abs(thetaTarget - theta) < 0.05) { p.phase = 3; p.t = 0; decided++; window.push(clock); } }
    else if (p.phase === 3) { p.t += dt * (p.verdict === "green" ? 0.35 : p.verdict === "amber" ? 0.6 : 0.9); if (p.t >= 1) p.phase = 4; }
  }
  pucks = pucks.filter((p) => p.phase !== 4);
  while (window.length && clock - window[0] > 6) window.shift();
  draw();
  frame = visible ? requestAnimationFrame(tick) : 0;
}
const run = () => { if (!frame && visible) { last = 0; frame = requestAnimationFrame(tick); } };
new IntersectionObserver((e) => { visible = e[e.length - 1].isIntersecting; if (visible) run(); }, { rootMargin: "120px 0px" }).observe(stage);
const setRate = (r) => { rate = Math.min(3.2, Math.max(0.6, r)); stage.setAttribute("aria-valuenow", rate.toFixed(1)); };
stage.addEventListener("pointermove", (ev) => { const b = svg.getBoundingClientRect(); const s = Math.min(1, Math.max(0, ((ev.clientX - b.left) / b.width - 0.2) / 0.6)); setRate(0.6 + s * 2.6); touring = false; clearTimeout(idleTimer); idleTimer = setTimeout(() => { touring = true; }, 4000); });
stage.addEventListener("keydown", (ev) => { if (ev.key === "ArrowRight" || ev.key === "ArrowUp") { setRate(rate + 0.2); ev.preventDefault(); } if (ev.key === "ArrowLeft" || ev.key === "ArrowDown") { setRate(rate - 0.2); ev.preventDefault(); } });
draw();
`;

const body = k.plateHtml({
  fig: "Fig 1",
  title: "Sorting turnstile",
  hint: "Hover to change the arrival rate",
  readout: "1.4 PR/s in · 0.0 decisions/s · 0 decided",
  keys: [
    { mark: "lit", label: "The puck in the gate: the one decision being made" },
    { mark: "raised", label: "Three feed chutes: the agent swarm" },
  ],
  caption: "Three chutes feed one rotary gate. Each puck gets a single verdict: green rides the exit belt (main), amber is set on the side shelf (a human), red drops through the trap (blocked). The gate turns one third per puck however fast the chutes feed it; the dial reads decisions per second.",
  body: `<div class="iso-stage" tabindex="0" role="slider" aria-label="Sorting turnstile: arrow keys change the arrival rate" aria-valuemin="0.6" aria-valuemax="3.2" aria-valuenow="1.4">${svg}</div>`,
});

const lampCss = `.iso-lamp[data-lamp="red"]{color:#F85149}.iso-lamp[data-lamp="amber"]{color:#D29922}.iso-lamp[data-lamp="green"]{color:#3FB950}`;
if (embed) {
  writeFileSync(join(HERE, "merge-gate.embed.html"), `<style>${k.ISO_CSS}${lampCss}</style>${body}<script type="module">${(kitScript() + live).replace(/<\/(script)/gi, "<\\/$1")}</script>`);
  console.log("wrote merge-gate.embed.html");
} else {
  const name = theme === "light" ? "merge-gate-light.html" : "merge-gate.html";
  writeFileSync(join(HERE, name), k.pageHtml({ title: "mergeone: sorting turnstile", theme, body: `<style>${lampCss}</style>${body}`, script: kitScript() + live }));
  console.log("wrote", name);
}
