import Foundation
import Vision

let file = URL(fileURLWithPath: CommandLine.arguments[1])
let request = VNDetectBarcodesRequest()
try VNImageRequestHandler(url: file, options: [:]).perform([request])
let codes = (request.results ?? []).filter { $0.symbology == .qr && $0.payloadStringValue != nil }
// Deliberately never print or save payment URLs/tokens.
print("QR_DETECTED=" + String(codes.count))
if codes.count != 1 { exit(1) }
