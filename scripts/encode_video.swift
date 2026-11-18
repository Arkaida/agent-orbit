import AppKit
import AVFoundation

let folder = URL(fileURLWithPath: CommandLine.arguments[1])
let output = URL(fileURLWithPath: CommandLine.arguments[2])
try? FileManager.default.removeItem(at: output)
let writer = try AVAssetWriter(outputURL: output, fileType: .mp4)
let input = AVAssetWriterInput(mediaType: .video, outputSettings: [AVVideoCodecKey: AVVideoCodecType.h264, AVVideoWidthKey: 1280, AVVideoHeightKey: 800, AVVideoCompressionPropertiesKey: [AVVideoAverageBitRateKey: 2500000]])
input.expectsMediaDataInRealTime = false
let adaptor = AVAssetWriterInputPixelBufferAdaptor(assetWriterInput: input, sourcePixelBufferAttributes: [kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32ARGB, kCVPixelBufferWidthKey as String: 1280, kCVPixelBufferHeightKey as String: 800, kCVPixelBufferCGImageCompatibilityKey as String: true, kCVPixelBufferCGBitmapContextCompatibilityKey as String: true])
writer.add(input)
writer.startWriting(); writer.startSession(atSourceTime: .zero)
for i in 0..<768 {
    while !input.isReadyForMoreMediaData {
        if writer.status == .failed { fatalError(writer.error!.localizedDescription) }
        Thread.sleep(forTimeInterval: 0.002)
    }
    autoreleasepool {
        let file = folder.appendingPathComponent(String(format: "%05d.png", i))
        let image = NSBitmapImageRep(data: try! Data(contentsOf: file))!.cgImage!
        var buffer: CVPixelBuffer?
        CVPixelBufferPoolCreatePixelBuffer(nil, adaptor.pixelBufferPool!, &buffer)
        CVPixelBufferLockBaseAddress(buffer!, [])
        let context = CGContext(data: CVPixelBufferGetBaseAddress(buffer!), width: 1280, height: 800, bitsPerComponent: 8, bytesPerRow: CVPixelBufferGetBytesPerRow(buffer!), space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGImageAlphaInfo.noneSkipFirst.rawValue)!
        context.draw(image, in: CGRect(x: 0, y: 0, width: 1280, height: 800))
        CVPixelBufferUnlockBaseAddress(buffer!, [])
        if !adaptor.append(buffer!, withPresentationTime: CMTime(value: Int64(i), timescale: 24)) { fatalError(writer.error?.localizedDescription ?? "Video append failed") }
    }
}
input.markAsFinished()
let done = DispatchSemaphore(value: 0)
writer.finishWriting { done.signal() }; done.wait()
guard writer.status == .completed else { fatalError(writer.error?.localizedDescription ?? "Video export failed") }
print("Exported 32-second H.264 demo video")
