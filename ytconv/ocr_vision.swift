// Text recognition with Apple's Vision framework (built into macOS - nothing to install).
// Compiled once by ytconv/ocr.py into .cache/ocr-vision.
//
// usage: ocr-vision LANGS IMAGE [IMAGE ...]
//   LANGS  comma-separated, e.g. "en-US" or "hi-IN,en-US"
// prints one JSON object per image per line:
//   {"image": path, "lines": [{"text": ..., "confidence": ..., "box": [x, y, w, h]}]}
// box is in 0-1 image coordinates, origin top-left.

import Foundation
import Vision

let args = CommandLine.arguments
if args.count < 3 {
    FileHandle.standardError.write("usage: ocr-vision LANGS IMAGE [IMAGE ...]\n".data(using: .utf8)!)
    exit(2)
}
let langs = args[1].split(separator: ",").map(String.init)

for path in args.dropFirst(2) {
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = true
    request.recognitionLanguages = langs
    var lines: [[String: Any]] = []
    do {
        try VNImageRequestHandler(url: URL(fileURLWithPath: path)).perform([request])
        for obs in request.results ?? [] {
            guard let top = obs.topCandidates(1).first else { continue }
            let b = obs.boundingBox
            lines.append([
                "text": top.string,
                "confidence": Double(top.confidence),
                "box": [Double(b.minX), Double(1 - b.maxY), Double(b.width), Double(b.height)],
            ])
        }
    } catch {
        FileHandle.standardError.write("\(path): \(error)\n".data(using: .utf8)!)
    }
    let obj: [String: Any] = ["image": path, "lines": lines]
    if let data = try? JSONSerialization.data(withJSONObject: obj),
       let s = String(data: data, encoding: .utf8) {
        print(s)
    }
}
