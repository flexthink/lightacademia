const expansionState = globalThis.__lightAcademiaWorkspaceTreeExpansionState || new Map();
globalThis.__lightAcademiaWorkspaceTreeExpansionState = expansionState;

const iconPaths = {
  file: [
    ["path", { d: "M14.5 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7.5L14.5 2z" }],
    ["polyline", { points: "14 2 14 8 20 8" }],
    ["line", { x1: "8", x2: "16", y1: "13", y2: "13" }],
    ["line", { x1: "8", x2: "16", y1: "17", y2: "17" }],
  ],
  note: [
    ["path", { d: "M14.5 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7.5L14.5 2z" }],
    ["polyline", { points: "14 2 14 8 20 8" }],
    ["line", { x1: "8", x2: "16", y1: "13", y2: "13" }],
    ["line", { x1: "8", x2: "16", y1: "17", y2: "17" }],
  ],
  wrench: [
    ["path", { d: "M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94z" }],
  ],
  folder: [
    ["path", { d: "M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z" }],
  ],
  folderOpen: [
    ["path", { d: "m6 14 1.5-2.9A2 2 0 0 1 9.24 10H20a2 2 0 0 1 1.94 2.5l-1.54 6a2 2 0 0 1-1.95 1.5H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h3.9a2 2 0 0 1 1.69.9l.81 1.2a2 2 0 0 0 1.67.9H18a2 2 0 0 1 2 2v2" }],
  ],
};

function renderIcon(element, name) {
  const namespace = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(namespace, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", "2");
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  for (const [tag, attributes] of iconPaths[name]) {
    const child = document.createElementNS(namespace, tag);
    for (const [attribute, value] of Object.entries(attributes)) {
      child.setAttribute(attribute, value);
    }
    svg.appendChild(child);
  }
  element.replaceChildren(svg);
}

export default function(component) {
  const { data, parentElement, setStateValue } = component;
  const root = parentElement.querySelector(".la-workspace-tree");
  if (!root || !data) {
    return;
  }

  const selected = data.selected || "";
  const expandedDepth = Number.isInteger(data.expandedDepth) ? data.expandedDepth : 2;
  const treeId = data.treeId || "workspace";
  const labels = data.labels || {};
  const icons = data.icons || {};
  const pinnedLast = new Set(data.pinnedLast || []);

  function sortedEntries(tree, parent = "") {
    return Object.entries(tree || {}).sort(([leftName, leftValue], [rightName, rightValue]) => {
      const leftPath = parent ? `${parent}/${leftName}` : leftName;
      const rightPath = parent ? `${parent}/${rightName}` : rightName;
      const leftPinnedLast = pinnedLast.has(leftPath);
      const rightPinnedLast = pinnedLast.has(rightPath);
      if (leftPinnedLast !== rightPinnedLast) {
        return leftPinnedLast ? 1 : -1;
      }
      const leftDirectory = leftValue !== null;
      const rightDirectory = rightValue !== null;
      if (leftDirectory !== rightDirectory) {
        return leftDirectory ? -1 : 1;
      }
      return (labels[leftPath] || leftName).localeCompare(labels[rightPath] || rightName);
    });
  }

  function defaultExpandedPaths(tree, depth = 0, parent = "", paths = new Set()) {
    for (const [name, value] of sortedEntries(tree, parent)) {
      const path = parent ? `${parent}/${name}` : name;
      if (value !== null) {
        if (depth < expandedDepth) {
          paths.add(path);
        }
        defaultExpandedPaths(value, depth + 1, path, paths);
      }
    }
    return paths;
  }

  let expandedPaths = expansionState.get(treeId);
  if (!expandedPaths) {
    expandedPaths = defaultExpandedPaths(data.tree);
    expansionState.set(treeId, expandedPaths);
  }

  if (selected) {
    const parts = selected.split("/");
    for (let index = 1; index < parts.length; index += 1) {
      expandedPaths.add(parts.slice(0, index).join("/"));
    }
  }

  function renderNode(name, value, path, depth) {
    const isDirectory = value !== null;
    const node = document.createElement("div");
    node.className = "la-workspace-tree-node";

    let expanded = isDirectory && expandedPaths.has(path);
    const row = document.createElement("div");
    row.className = `la-workspace-tree-row${selected === path ? " is-selected" : ""}`;
    row.style.paddingLeft = `${depth * 18 + 6}px`;
    row.setAttribute("role", "treeitem");
    row.setAttribute("tabindex", "0");
    row.setAttribute("aria-selected", selected === path ? "true" : "false");

    const chevron = document.createElement("span");
    chevron.className = "la-workspace-tree-chevron";
    chevron.textContent = isDirectory ? (expanded ? "▾" : "▸") : "";
    if (isDirectory) {
      chevron.setAttribute("role", "button");
      chevron.setAttribute("tabindex", "0");
      chevron.setAttribute("aria-label", `${expanded ? "Collapse" : "Expand"} ${name}`);
    }

    const icon = document.createElement("span");
    icon.className = `la-workspace-tree-icon${isDirectory ? "" : " la-workspace-tree-file-icon"}`;
    const configuredIcon = icons[path];
    renderIcon(
      icon,
      configuredIcon in iconPaths
        ? configuredIcon
        : (isDirectory ? (expanded ? "folderOpen" : "folder") : "file"),
    );

    const label = document.createElement("span");
    label.className = "la-workspace-tree-label";
    label.textContent = labels[path] || name;

    row.append(chevron, icon, label);
    node.appendChild(row);

    let children = null;
    if (isDirectory) {
      children = document.createElement("div");
      children.className = "la-workspace-tree-children";
      children.hidden = !expanded;
      for (const [childName, childValue] of sortedEntries(value, path)) {
        children.appendChild(renderNode(childName, childValue, `${path}/${childName}`, depth + 1));
      }
      node.appendChild(children);
    }

    const selectRow = () => {
      if (selected !== path) {
        setStateValue("selected", path);
      }
    };
    row.onclick = selectRow;
    row.onkeydown = (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        selectRow();
      }
    };

    if (isDirectory && children) {
      chevron.onclick = (event) => {
        event.stopPropagation();
        expanded = !expanded;
        if (expanded) {
          expandedPaths.add(path);
        } else {
          expandedPaths.delete(path);
        }
        children.hidden = !expanded;
        chevron.textContent = expanded ? "▾" : "▸";
        chevron.setAttribute("aria-label", `${expanded ? "Collapse" : "Expand"} ${name}`);
        renderIcon(
          icon,
          configuredIcon in iconPaths
            ? configuredIcon
            : (expanded ? "folderOpen" : "folder"),
        );
      };
      chevron.onkeydown = (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          event.stopPropagation();
          chevron.click();
        }
      };
    }

    return node;
  }

  root.replaceChildren();
  for (const [name, value] of sortedEntries(data.tree)) {
    root.appendChild(renderNode(name, value, name, 0));
  }
}
