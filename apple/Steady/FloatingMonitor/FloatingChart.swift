#if os(macOS)
import SteadyKit
import SwiftUI

/// The floating monitor's sweep chart: a heart-monitor trace written left to right once a minute,
/// fading as it ages, with lost pings marked along the bottom. The geometry is `Sweep` (SteadyKit).
struct FloatingChart: View {
    let samples: [Sweep.Sample]
    let now: Double
    let ceiling: Double
    let color: Color

    private func point(_ sample: Sweep.Point) -> CGPoint {
        CGPoint(x: CGFloat(sample.x), y: CGFloat(sample.y))
    }

    var body: some View {
        Canvas { context, size in
            let width = Double(size.width), height = Double(size.height)
            let layout = Sweep.layout(samples, now: now, width: width, height: height, ceiling: ceiling)

            var grid = Path()
            for fraction in [0.25, 0.5, 0.75] {
                let y = CGFloat(height - fraction * (height - Sweep.padTop))
                grid.move(to: CGPoint(x: 0, y: y))
                grid.addLine(to: CGPoint(x: size.width, y: y))
            }
            for second in stride(from: 10.0, to: Sweep.sweepSeconds, by: 10) {
                let x = CGFloat(second / Sweep.sweepSeconds * width)
                grid.move(to: CGPoint(x: x, y: CGFloat(Sweep.padTop)))
                grid.addLine(to: CGPoint(x: x, y: size.height))
            }
            context.stroke(grid, with: .color(.primary.opacity(0.1)), lineWidth: 1)

            let style = StrokeStyle(lineWidth: 1.6, lineCap: .round, lineJoin: .round)
            for line in layout.lines {
                for index in line.indices.dropFirst() {
                    var segment = Path()
                    segment.move(to: point(line[index - 1]))
                    segment.addLine(to: point(line[index]))
                    // Older trace fades, like the phosphor of a monitor.
                    context.stroke(segment, with: .color(color.opacity(0.2 + 0.8 * (1 - line[index].age))), style: style)
                }
            }

            for mark in layout.lost {
                let rect = CGRect(x: CGFloat(mark.x) - 1, y: size.height - 8, width: 2, height: 8)
                context.fill(Path(rect), with: .color(.red.opacity(0.3 + 0.7 * (1 - mark.age))))
            }

            if let pen = layout.pen {
                let dot = CGRect(x: CGFloat(pen.x) - 2.5, y: CGFloat(pen.y) - 2.5, width: 5, height: 5)
                context.fill(Path(ellipseIn: dot), with: .color(color))
            }
        }
    }
}
#endif
