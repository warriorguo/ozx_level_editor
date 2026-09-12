#!/usr/bin/env swift
// MakeIcon.swift — generates the 1024×1024 master PNG for the OZX Level Studio
// app icon. Pure Core Graphics, no external dependencies; the Makefile turns
// this into an .icns with sips + iconutil.
//
//   swift tools/MakeIcon.swift <output-png-path> [--compact]
//
// `--compact` draws the small-size variant. At 16×16 the detail that makes the
// large icon work — hairline grid, soft glow, a thin plus inside a thin
// diamond — collapses into an indistinct blob. macOS takes different artwork
// per slot, so the small sizes get a heavier, plainer mark instead of a
// downscaled busy one. This is the same reason Apple ships separate 16pt
// artwork rather than resampling.
//
// The mark is the one the app already wears in its own title bar
// (`.brand-mark` in web/styles.css): a square rotated 45° with a plus through
// it, acid green on the near-black ground, over the same faint grid the page
// uses. Reusing it means the Dock icon and the window header agree instead of
// being two unrelated logos.
//
// Everything is scaled from `size`, so the proportions hold if the master is
// ever regenerated larger. Strokes are deliberately heavier than the 1px CSS
// border: at 16×16 a hairline outline disappears entirely.

import AppKit
import CoreGraphics
import Foundation

// MARK: - Palette (from web/styles.css)

let size: CGFloat = 1024
// Apple's squircle is not public API; ~22.37% corner radius is the standard
// approximation and matches what icon templates ship.
let cornerRadius: CGFloat = size * 0.2237

let groundTop    = CGColor(red: 0x1D / 255, green: 0x24 / 255, blue: 0x20 / 255, alpha: 1) // --panel-2
let groundBottom = CGColor(red: 0x0D / 255, green: 0x10 / 255, blue: 0x0F / 255, alpha: 1) // body bg
let acid         = CGColor(red: 0xD6 / 255, green: 0xF4 / 255, blue: 0x5D / 255, alpha: 1) // --acid
let gridColor    = CGColor(red: 1, green: 1, blue: 1, alpha: 0.030)

// The page's background grid is 24px on a ~1280px window; scaled here so the
// icon reads as the same surface rather than a coincidentally similar one.
let gridStep: CGFloat = size / 16
let gridLineWidth: CGFloat = size / 512

// Mark geometry. The diamond is a square rotated 45°, so its width on screen
// is side * √2 — `diamondSide` is chosen to land that diagonal at ~58% of the
// canvas, leaving the margin Apple's grid expects.
// The compact variant trades detail for legibility: a fatter outline, a plus
// that fills more of the interior, and no glow to smear the two together.
let diamondSide: CGFloat = size * (compact ? 0.430 : 0.410)
let markStroke: CGFloat = size * (compact ? 0.060 : 0.034)
let plusArm: CGFloat = diamondSide * (compact ? 0.62 : 0.46)
let plusThickness: CGFloat = size * (compact ? 0.090 : 0.034)
let glowRadius: CGFloat = size * 0.055

// MARK: - Arguments

let args = Array(CommandLine.arguments.dropFirst())
guard let outputPath = args.first(where: { !$0.hasPrefix("--") }) else {
    FileHandle.standardError.write(
        "usage: MakeIcon.swift <output-png-path> [--compact]\n".data(using: .utf8)!)
    exit(2)
}
let compact = args.contains("--compact")

// MARK: - Canvas

guard let context = CGContext(
    data: nil,
    width: Int(size), height: Int(size),
    bitsPerComponent: 8, bytesPerRow: 0,
    space: CGColorSpace(name: CGColorSpace.sRGB)!,
    bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)
else {
    FileHandle.standardError.write("could not create bitmap context\n".data(using: .utf8)!)
    exit(1)
}

let bounds = CGRect(x: 0, y: 0, width: size, height: size)
let squircle = CGPath(roundedRect: bounds,
                      cornerWidth: cornerRadius, cornerHeight: cornerRadius,
                      transform: nil)

// Ground — clipped to the rounded rect so nothing paints into the corners.
context.saveGState()
context.addPath(squircle)
context.clip()

let gradient = CGGradient(colorsSpace: CGColorSpace(name: CGColorSpace.sRGB)!,
                          colors: [groundTop, groundBottom] as CFArray,
                          locations: [0, 1])!
context.drawLinearGradient(gradient,
                           start: CGPoint(x: 0, y: size),
                           end: CGPoint(x: 0, y: 0),
                           options: [])

// The page's hairline grid, barely there — texture rather than decoration.
context.setStrokeColor(gridColor)
context.setLineWidth(gridLineWidth)
var offset: CGFloat = compact ? size * 2 : 0   // compact: skip the grid entirely
while offset <= size {
    context.move(to: CGPoint(x: offset, y: 0))
    context.addLine(to: CGPoint(x: offset, y: size))
    context.move(to: CGPoint(x: 0, y: offset))
    context.addLine(to: CGPoint(x: size, y: offset))
    offset += gridStep
}
context.strokePath()

// MARK: - The mark

let centre = CGPoint(x: size / 2, y: size / 2)

/// The diamond: a square rotated 45°, stroked not filled — the mark is an
/// outline, exactly as `.brand-mark` is a 1px border in the page.
func diamondPath() -> CGPath {
    let rotate = CGAffineTransform.identity
        .translatedBy(x: centre.x, y: centre.y)
        .rotated(by: .pi / 4)
    let square = CGRect(x: -diamondSide / 2, y: -diamondSide / 2,
                        width: diamondSide, height: diamondSide)
    // Slightly rounded corners; a hard point at this stroke weight reads as a
    // glitch once the icon is scaled down.
    let path = CGMutablePath()
    path.addPath(CGPath(roundedRect: square,
                        cornerWidth: markStroke * 0.6,
                        cornerHeight: markStroke * 0.6,
                        transform: nil),
                 transform: rotate)
    return path
}

/// The plus, filled and axis-aligned — staying upright against the rotated
/// diamond is what gives the mark its off-kilter, tool-ish feel.
func plusPath() -> CGPath {
    let path = CGMutablePath()
    let half = plusArm / 2
    let cap = plusThickness / 2
    path.addPath(CGPath(roundedRect:
        CGRect(x: centre.x - half, y: centre.y - cap,
               width: plusArm, height: plusThickness),
        cornerWidth: cap, cornerHeight: cap, transform: nil))
    path.addPath(CGPath(roundedRect:
        CGRect(x: centre.x - cap, y: centre.y - half,
               width: plusThickness, height: plusArm),
        cornerWidth: cap, cornerHeight: cap, transform: nil))
    return path
}

let diamond = diamondPath()
let plus = plusPath()

/// Kept in one function so the glow pass and the crisp pass cannot drift apart.
///
/// The two variants differ in more than weight. At large sizes the mark is an
/// outlined diamond with an acid plus inside it, matching `.brand-mark`. At
/// 16px that cannot resolve: the outline is barely a pixel and an acid plus
/// inside an acid outline merges into a lozenge. So the compact variant
/// inverts — a solid diamond with the plus knocked out of it in the ground
/// colour. Same mark, but carried by contrast instead of by line work.
func drawMark(in context: CGContext) {
    if compact {
        context.setFillColor(acid)
        context.addPath(diamond)
        context.fillPath()

        context.setFillColor(groundBottom)
        context.addPath(plus)
        context.fillPath()
        return
    }

    context.setStrokeColor(acid)
    context.setLineWidth(markStroke)
    context.addPath(diamond)
    context.strokePath()

    context.setFillColor(acid)
    context.addPath(plus)
    context.fillPath()
}

// Glow first, so the mark sits on it rather than in it. Omitted when compact:
// at 16px the halo merges with the strokes and fills the diamond in.
if !compact {
    context.saveGState()
    context.setShadow(offset: .zero, blur: glowRadius,
                      color: CGColor(red: 0xD6 / 255, green: 0xF4 / 255,
                                     blue: 0x5D / 255, alpha: 0.55))
    drawMark(in: context)
    context.restoreGState()
}

// Redraw crisply on top — the shadow pass softens edges.
drawMark(in: context)

context.restoreGState()

// A hairline inner edge, the way the app's panels are bordered. Stops the
// icon dissolving into a dark Dock.
context.addPath(CGPath(roundedRect: bounds.insetBy(dx: 1.5, dy: 1.5),
                       cornerWidth: cornerRadius, cornerHeight: cornerRadius,
                       transform: nil))
context.setStrokeColor(CGColor(red: 1, green: 1, blue: 1, alpha: 0.07))
context.setLineWidth(3)
context.strokePath()

// MARK: - Write

guard let image = context.makeImage() else {
    FileHandle.standardError.write("could not render image\n".data(using: .utf8)!)
    exit(1)
}
let rep = NSBitmapImageRep(cgImage: image)
rep.size = NSSize(width: size, height: size)
guard let png = rep.representation(using: .png, properties: [:]) else {
    FileHandle.standardError.write("could not encode PNG\n".data(using: .utf8)!)
    exit(1)
}
do {
    try png.write(to: URL(fileURLWithPath: outputPath))
    print("wrote \(outputPath) (\(Int(size))×\(Int(size))\(compact ? ", compact" : ""))")
} catch {
    FileHandle.standardError.write("write failed: \(error)\n".data(using: .utf8)!)
    exit(1)
}
