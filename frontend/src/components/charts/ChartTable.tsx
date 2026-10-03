/** The table-view twin of a chart: every plotted value, readable without hovering. */
export function ChartTable({ head, rows }: { head: string[]; rows: (string | number)[][] }) {
  return (
    <details>
      <summary className="small">Table view</summary>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              {head.map((h, i) => (
                <th key={h} className={i > 0 ? "num" : undefined}>
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i}>
                {r.map((c, j) => (
                  <td key={j} className={j > 0 ? "num" : undefined}>
                    {c}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  );
}
