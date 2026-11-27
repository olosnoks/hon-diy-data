/**
 * uPlot in a React box: built when the options change, fed new data in
 * place, sized to its container.
 */
import { useEffect, useRef } from 'react';
import uPlot from 'uplot';
import 'uplot/dist/uPlot.min.css';

export type ChartOptions = Omit<uPlot.Options, 'width' | 'height'>;

export function UPlotChart({ options, data, label }: { options: ChartOptions; data: uPlot.AlignedData; label: string }) {
  const box = useRef<HTMLDivElement>(null);
  const plot = useRef<uPlot | null>(null);
  const dataRef = useRef(data);
  dataRef.current = data;

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const size = () => ({ width: Math.max(200, el.clientWidth || 800), height: Math.max(160, el.clientHeight || 400) });
    const u = new uPlot({ ...options, ...size() }, dataRef.current, el);
    plot.current = u;
    const ro = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(() => u.setSize(size())) : null;
    ro?.observe(el);
    return () => {
      ro?.disconnect();
      u.destroy();
      plot.current = null;
    };
  }, [options]);

  useEffect(() => {
    plot.current?.setData(data);
  }, [data]);

  return <div ref={box} role="img" aria-label={label} style={{ position: 'absolute', inset: 0 }} />;
}
