import Foundation
import Vision
import AppKit

let path = CommandLine.arguments[1]
let url = URL(fileURLWithPath: path)
guard let image = NSImage(contentsOf: url), let tiff = image.tiffRepresentation, let bitmap = NSBitmapImageRep(data: tiff), let cgImage = bitmap.cgImage else {
    fputs("Failed to load image\n", stderr)
    exit(1)
}
let request = VNRecognizeTextRequest { request, error in
    if let error = error {
        fputs("OCR error: \(error)\n", stderr)
        exit(1)
    }
    let observations = request.results as? [VNRecognizedTextObservation] ?? []
    for obs in observations {
        guard let candidate = obs.topCandidates(1).first else { continue }
        let b = obs.boundingBox
        // Convert normalized Vision coords (origin bottom-left) to image coords (origin top-left)
        let x = Int(b.origin.x * CGFloat(cgImage.width))
        let y = Int((1.0 - b.origin.y - b.height) * CGFloat(cgImage.height))
        let w = Int(b.width * CGFloat(cgImage.width))
        let h = Int(b.height * CGFloat(cgImage.height))
        print("[\(x),\(y),\(w),\(h)] \(String(format: "%.2f", candidate.confidence)) \(candidate.string)")
    }
}
request.recognitionLevel = .accurate
request.usesLanguageCorrection = true
if #available(macOS 11.0, *) {
    request.recognitionLanguages = ["zh-Hans", "en-US"]
}
let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])
do {
    try handler.perform([request])
} catch {
    fputs("Failed to perform OCR: \(error)\n", stderr)
    exit(1)
}
