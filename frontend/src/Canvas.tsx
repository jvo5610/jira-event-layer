import { t, useLanguage, locale } from "./i18n";
import { memo, useEffect, useMemo } from "react";
import {
  ReactFlow,
  Background,
  Controls,
  Handle,
  Position,
  BackgroundVariant,
  useNodesState,
  type NodeProps,
} from "@xyflow/react";
import {
  Zap,
  Filter,
  Plus,
  GitBranch,
  MessageSquare,
  FilePlus2,
  Copy,
  Pencil,
  Search,
  Link2,
  ArrowRightLeft,
} from "lucide-react";
import "@xyflow/react/dist/style.css";
import { Rule, actionLabels } from "./model";

const icons: Record<string, any> = {
  "jira.issue.get": Search,
  "jira.issue.create": FilePlus2,
  "jira.issue.clone": Copy,
  "jira.issue.edit": Pencil,
  "jira.issue.transition": ArrowRightLeft,
  "jira.comment.add": MessageSquare,
  "jira.issue.link": Link2,
  "bitbucket.pipeline": GitBranch,
};
const Module = memo(function Module({ data, selected }: NodeProps) {
  const kind = String(data.kind);
  const Icon =
    kind === "trigger"
      ? Zap
      : kind === "filter"
        ? Filter
        : kind === "add"
          ? Plus
          : (icons[String(data.type)] ?? Zap);
  const provider = String(data.type ?? "").startsWith("bitbucket")
    ? "bitbucket"
    : "jira";
  return (
    <div
      className={
        "module " + kind + " " + provider + (selected ? " is-selected" : "")
      }
    >
      {kind !== "trigger" && (
        <Handle type="target" position={Position.Left} isConnectable={false} />
      )}
      <div className="module-orbit">
        <Icon size={kind === "add" ? 25 : 34} strokeWidth={1.6} />
        {kind === "trigger" && (
          <span className="instant-badge">
            <Zap size={13} />
          </span>
        )}
        {typeof data.index === "number" && (
          <span className="step-badge">{data.index + 1}</span>
        )}
      </div>
      <div className="module-title">{String(data.label)}</div>
      <div className="module-subtitle">{String(data.subtitle ?? "")}</div>
      {kind !== "add" && (
        <Handle type="source" position={Position.Right} isConnectable={false} />
      )}
    </div>
  );
});
const nodeTypes = { module: Module };
export default function Canvas({
  rule,
  selected,
  onSelect,
  positions,
  onPosition,
}: {
  rule: Rule;
  selected: string;
  onSelect: (id: string) => void;
  positions: Record<string, { x: number; y: number }>;
  onPosition: (id: string, p: { x: number; y: number }) => void;
}) {
  const language = useLanguage();
  const signature = JSON.stringify({
    language,
    trigger: rule.trigger,
    types: rule.actions.map((a) => a.type),
  });
  const nodes = useMemo(
    () =>
      [
        {
          id: "trigger",
          type: "module",
          position: positions.trigger ?? { x: 70, y: 190 },
          data: {
            kind: "trigger",
            label:
              rule.trigger.source === "jira" ? "Jira" : t("Evento de prueba"),
            subtitle: rule.trigger.event,
          },
        },
        {
          id: "filter",
          type: "module",
          position: positions.filter ?? { x: 310, y: 190 },
          data: {
            kind: "filter",
            label: t("Condiciones"),
            subtitle: t("Filtrar los eventos"),
          },
        },
        ...rule.actions.map((a, i) => ({
          id: "a" + i,
          type: "module",
          position: positions["a" + i] ?? { x: 550 + i * 245, y: 190 },
          data: {
            kind: "action",
            type: a.type,
            index: i,
            label: a.type.startsWith("bitbucket") ? "Bitbucket" : "Jira",
            subtitle: t(actionLabels[a.type]) ?? a.type,
          },
        })),
        ...(rule.actions.length < 10
          ? [
              {
                id: "add",
                type: "module",
                position: { x: 550 + rule.actions.length * 245, y: 190 },
                data: {
                  kind: "add",
                  label: t("Agregar módulo"),
                  subtitle: t("Elegí una acción"),
                },
              },
            ]
          : []),
      ].map((n) => ({ ...n, selected: n.id === selected, deletable: false })),
    [signature, positions, selected],
  );
  const edges = useMemo(
    () =>
      nodes.slice(1).map((n, i) => ({
        id: "e" + i,
        source: nodes[i].id,
        target: n.id,
        type: "smoothstep",
        style: { stroke: "#c5c1d0", strokeWidth: 2, strokeDasharray: "3 7" },
      })),
    [nodes],
  );
  const [liveNodes, setLiveNodes, onNodesChange] = useNodesState(nodes);
  useEffect(() => setLiveNodes(nodes), [nodes, setLiveNodes]);
  return (
    <ReactFlow
      nodes={liveNodes}
      onNodesChange={onNodesChange}
      edges={edges}
      nodeTypes={nodeTypes}
      onNodeClick={(_, node) => onSelect(node.id)}
      onNodeDragStop={(_, node) => onPosition(node.id, node.position)}
      nodesConnectable={false}
      edgesReconnectable={false}
      deleteKeyCode={null}
      fitView
      fitViewOptions={{ padding: 0.22, maxZoom: 1 }}
      minZoom={0.2}
      maxZoom={1.6}
      proOptions={{ hideAttribution: false }}
    >
      <Background
        variant={BackgroundVariant.Dots}
        gap={22}
        size={1}
        color="#dad6e2"
      />
      <Controls showInteractive={false} />
    </ReactFlow>
  );
}
