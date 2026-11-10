"use client";

import { useCallback, useMemo, useState } from "react";

export type CanvasRecord = {
  id: string;
  source_label: string | null;
  content: string;
  source_id?: string | null;
  source_version_id?: string | null;
};

export type CanvasRelationship = {
  id: string;
  evidence_id_a: string;
  evidence_id_b: string;
  relation_type: "supports" | "contradicts" | "contextualizes" | "derived_from";
  rationale: string;
  provenance_note: string;
  created_by?: string;
  updated_at: string;
};

type Props = {
  records: CanvasRecord[];
  relationships: CanvasRelationship[];
  onOpenEvidence?: (id: string) => void;
};

const RELATION_META: Record<
  CanvasRelationship["relation_type"],
  { label: string; color: string }
> = {
  supports: { label: "Supports", color: "#1f6f5c" },
  contradicts: { label: "Contradicts", color: "#a33b3b" },
  contextualizes: { label: "Contextualizes", color: "#b7791f" },
  derived_from: { label: "Derived from", color: "#3b6ea5" },
};

const CARD_W = 168;
const CARD_H = 56;
const CENTER = { x: 430, y: 290 };

function shortLabel(record: CanvasRecord): string {
  const label = record.source_label?.trim() || `Evidence ${record.id.slice(0, 6)}`;
  return label.length > 24 ? `${label.slice(0, 23)}…` : label;
}

function boundaryPoint(
  fromX: number,
  fromY: number,
  toX: number,
  toY: number,
  halfWidth: number,
  halfHeight: number,
) {
  const dx = toX - fromX;
  const dy = toY - fromY;
  if (dx === 0 && dy === 0) return { x: fromX, y: fromY };
  const scaleX = dx === 0 ? Number.POSITIVE_INFINITY : halfWidth / Math.abs(dx);
  const scaleY = dy === 0 ? Number.POSITIVE_INFINITY : halfHeight / Math.abs(dy);
  const scale = Math.min(scaleX, scaleY);
  return { x: fromX + dx * scale, y: fromY + dy * scale };
}

function curvePath(ax: number, ay: number, bx: number, by: number, offset: number): string {
  const mx = (ax + bx) / 2;
  const my = (ay + by) / 2;
  const dx = bx - ax;
  const dy = by - ay;
  const length = Math.hypot(dx, dy) || 1;
  const cx = mx + (-dy / length) * offset;
  const cy = my + (dx / length) * offset;
  return `M ${ax} ${ay} Q ${cx} ${cy} ${bx} ${by}`;
}

function curveMidpoint(ax: number, ay: number, bx: number, by: number, offset: number) {
  const mx = (ax + bx) / 2;
  const my = (ay + by) / 2;
  const dx = bx - ax;
  const dy = by - ay;
  const length = Math.hypot(dx, dy) || 1;
  const cx = mx + (-dy / length) * offset;
  const cy = my + (dx / length) * offset;
  // Quadratic curve at t = 0.5
  return { x: 0.25 * ax + 0.5 * cx + 0.25 * bx, y: 0.25 * ay + 0.5 * cy + 0.25 * by };
}

type Selection = { kind: "node"; id: string } | { kind: "edge"; id: string } | null;

export default function RelationshipCanvas({ records, relationships, onOpenEvidence }: Props) {
  const [selection, setSelection] = useState<Selection>(null);
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [drag, setDrag] = useState<{ x: number; y: number; panX: number; panY: number } | null>(null);

  const nodes = useMemo(() => {
    const referenced = new Set<string>();
    for (const relationship of relationships) {
      referenced.add(relationship.evidence_id_a);
      referenced.add(relationship.evidence_id_b);
    }
    const visible = records.filter((record) => referenced.has(record.id));
    const count = visible.length;
    // Keep every card inside the 860x580 viewBox, including its half-height.
    const radiusX = count <= 1 ? 0 : Math.min(326, 130 + count * 24);
    const radiusY = count <= 1 ? 0 : Math.min(238, 100 + count * 18);
    return visible.map((record, index) => {
      const angle = (2 * Math.PI * index) / Math.max(count, 1) - Math.PI / 2;
      return {
        record,
        x: CENTER.x + radiusX * Math.cos(angle),
        y: CENTER.y + radiusY * Math.sin(angle),
      };
    });
  }, [records, relationships]);

  const nodeById = useMemo(() => new Map(nodes.map((node) => [node.record.id, node])), [nodes]);

  const edges = useMemo(() => relationships.flatMap((relationship, index) => {
    const a = nodeById.get(relationship.evidence_id_a);
    const b = nodeById.get(relationship.evidence_id_b);
    if (!a || !b) return [];
    if (a.record.id === b.record.id) {
      const top = a.y - CARD_H / 2;
      return [{
        relationship,
        path: `M ${a.x - 28} ${top} C ${a.x - 86} ${top - 74}, ${a.x + 86} ${top - 74}, ${a.x + 28} ${top}`,
        midpoint: { x: a.x, y: top - 48 },
      }];
    }
    const offset = (index % 2 === 0 ? 1 : -1) * (26 + (index % 3) * 10);
    const start = boundaryPoint(a.x, a.y, b.x, b.y, CARD_W / 2 + 5, CARD_H / 2 + 5);
    const end = boundaryPoint(b.x, b.y, a.x, a.y, CARD_W / 2 + 5, CARD_H / 2 + 5);
    return [{
      relationship,
      path: curvePath(start.x, start.y, end.x, end.y, offset),
      midpoint: curveMidpoint(start.x, start.y, end.x, end.y, offset),
    }];
  }), [relationships, nodeById]);

  const selectedNode = selection?.kind === "node" ? nodeById.get(selection.id)?.record ?? null : null;
  const selectedEdge = selection?.kind === "edge"
    ? edges.find((edge) => edge.relationship.id === selection.id) ?? null
    : null;
  const selectedNodeLinks = selectedNode
    ? relationships.filter((relationship) =>
        relationship.evidence_id_a === selectedNode.id || relationship.evidence_id_b === selectedNode.id)
    : [];

  const reset = useCallback(() => {
    setZoom(1);
    setPan({ x: 0, y: 0 });
    setSelection(null);
  }, []);

  const transform = `translate(${CENTER.x + pan.x} ${CENTER.y + pan.y}) scale(${zoom}) translate(${-CENTER.x} ${-CENTER.y})`;

  return (
    <div className="relationship-canvas">
      <div className="relationship-canvas-toolbar">
        <div className="relationship-canvas-legend" aria-hidden="true">
          {Object.values(RELATION_META).map((meta) => (
            <span key={meta.label}><i style={{ background: meta.color }} />{meta.label}</span>
          ))}
        </div>
        <span className="relationship-canvas-summary">{nodes.length} {nodes.length === 1 ? "record" : "records"} · {edges.length} {edges.length === 1 ? "link" : "links"}</span>
        <div className="relationship-canvas-controls">
          <button type="button" onClick={() => setZoom((value) => Math.min(1.8, Number((value + 0.15).toFixed(2))))} aria-label="Zoom in">+</button>
          <button type="button" onClick={() => setZoom((value) => Math.max(0.6, Number((value - 0.15).toFixed(2))))} aria-label="Zoom out">−</button>
          <button type="button" onClick={reset}>Reset</button>
        </div>
      </div>

      <div className="relationship-canvas-viewport">
      <svg
        className="relationship-canvas-svg"
        viewBox="0 0 860 580"
        role="img"
        aria-label={`Recorded relationship canvas, ${nodes.length} evidence records and ${edges.length} links`}
        onPointerDown={(event) => {
          if (event.target !== event.currentTarget) return;
          setDrag({ x: event.clientX, y: event.clientY, panX: pan.x, panY: pan.y });
        }}
        onPointerMove={(event) => {
          if (!drag) return;
          setPan({ x: drag.panX + event.clientX - drag.x, y: drag.panY + event.clientY - drag.y });
        }}
        onPointerUp={() => setDrag(null)}
        onPointerLeave={() => setDrag(null)}
        onClick={(event) => {
          if (event.target === event.currentTarget) setSelection(null);
        }}
      >
        <defs>
          {Object.entries(RELATION_META).map(([type, meta]) => (
            <marker key={type} id={`arrow-${type}`} markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto">
              <path d="M0,0 L7,3 L0,6 z" fill={meta.color} />
            </marker>
          ))}
        </defs>
        <g transform={transform}>
          {edges.map(({ relationship, path, midpoint }) => {
            const meta = RELATION_META[relationship.relation_type];
            const isSelected = selection?.kind === "edge" && selection.id === relationship.id;
            const isConnected = selection?.kind === "node" &&
              (relationship.evidence_id_a === selection.id || relationship.evidence_id_b === selection.id);
            return (
              <g
                key={relationship.id}
                className="relationship-canvas-edge"
                role="button"
                tabIndex={0}
                aria-label={`${meta.label} link: ${relationship.rationale}`}
                onClick={(event) => {
                  event.stopPropagation();
                  setSelection({ kind: "edge", id: relationship.id });
                }}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    setSelection({ kind: "edge", id: relationship.id });
                  }
                }}
              >
                <path d={path} fill="none" stroke="transparent" strokeWidth={16} pointerEvents="stroke" />
                <path
                  d={path}
                  fill="none"
                  stroke={meta.color}
                  strokeWidth={isSelected ? 3.4 : isConnected ? 2.6 : 1.6}
                  strokeOpacity={isSelected || isConnected || selection === null ? 0.9 : 0.35}
                  markerEnd={`url(#arrow-${relationship.relation_type})`}
                />
                <circle cx={midpoint.x} cy={midpoint.y} r={isSelected ? 5 : 3.5} fill={meta.color} />
                <text x={midpoint.x} y={midpoint.y - 8} textAnchor="middle" className="relationship-canvas-edge-label">
                  {meta.label}
                </text>
              </g>
            );
          })}

          {nodes.map(({ record, x, y }) => {
            const isSelected = selection?.kind === "node" && selection.id === record.id;
            return (
              <g
                key={record.id}
                className={`relationship-canvas-node${isSelected ? " is-selected" : ""}`}
                role="button"
                tabIndex={0}
                aria-label={`Evidence ${shortLabel(record)}`}
                onClick={(event) => {
                  event.stopPropagation();
                  setSelection({ kind: "node", id: record.id });
                }}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    setSelection({ kind: "node", id: record.id });
                  }
                }}
              >
                <rect x={x - CARD_W / 2} y={y - CARD_H / 2} width={CARD_W} height={CARD_H} rx={10} />
                <text x={x} y={y - 4} textAnchor="middle" className="relationship-canvas-node-title">
                  {shortLabel(record)}
                </text>
                <text x={x} y={y + 15} textAnchor="middle" className="relationship-canvas-node-meta">
                  {relationshipCount(relationships, record.id)}{" "}
                  {relationshipCount(relationships, record.id) === 1 ? "link" : "links"}
                </text>
              </g>
            );
          })}
        </g>
      </svg>
      </div>

      <div className="relationship-canvas-details" role="region" aria-label="Relationship details" aria-live="polite">
        {selectedEdge ? (
          <>
            <h3>{RELATION_META[selectedEdge.relationship.relation_type].label}</h3>
            <p><strong>From:</strong> {shortLabel(nodeById.get(selectedEdge.relationship.evidence_id_a)?.record ?? { id: selectedEdge.relationship.evidence_id_a, source_label: null, content: "" })}</p>
            <p><strong>To:</strong> {shortLabel(nodeById.get(selectedEdge.relationship.evidence_id_b)?.record ?? { id: selectedEdge.relationship.evidence_id_b, source_label: null, content: "" })}</p>
            <p><strong>Rationale:</strong> {selectedEdge.relationship.rationale}</p>
            <p><strong>Recorded basis:</strong> {selectedEdge.relationship.provenance_note}</p>
            <p className="relationship-canvas-meta">
              {selectedEdge.relationship.created_by ? `Recorded by ${selectedEdge.relationship.created_by} · ` : ""}
              Updated {new Date(selectedEdge.relationship.updated_at).toLocaleString()}
            </p>
            <div className="relationship-canvas-actions">
              <button type="button" onClick={() => onOpenEvidence?.(selectedEdge.relationship.evidence_id_a)}>Open evidence A</button>
              <button type="button" onClick={() => onOpenEvidence?.(selectedEdge.relationship.evidence_id_b)}>Open evidence B</button>
            </div>
          </>
        ) : selectedNode ? (
          <>
            <h3>{shortLabel(selectedNode)}</h3>
            <p className="relationship-canvas-meta">{selectedNode.content.slice(0, 220)}{selectedNode.content.length > 220 ? "…" : ""}</p>
            <ul>
              {selectedNodeLinks.map((relationship) => {
                const otherId = relationship.evidence_id_a === selectedNode.id
                  ? relationship.evidence_id_b
                  : relationship.evidence_id_a;
                const other = nodeById.get(otherId)?.record;
                return (
                  <li key={relationship.id}>
                    <strong>{RELATION_META[relationship.relation_type].label}</strong>{" "}
                    {other ? shortLabel(other) : otherId.slice(0, 8)} — {relationship.rationale}
                  </li>
                );
              })}
            </ul>
            <div className="relationship-canvas-actions">
              <button type="button" onClick={() => onOpenEvidence?.(selectedNode.id)}>Open evidence</button>
            </div>
          </>
        ) : (
          <p className="relationship-canvas-meta">
            Select a card for its links, or a link for its rationale and recorded basis. Only persisted links are shown; nothing is inferred.
          </p>
        )}
      </div>
    </div>
  );
}

function relationshipCount(relationships: CanvasRelationship[], id: string): number {
  return relationships.filter((relationship) =>
    relationship.evidence_id_a === id || relationship.evidence_id_b === id).length;
}
