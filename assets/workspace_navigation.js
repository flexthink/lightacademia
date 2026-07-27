function encodeRoute(project, note) {
  let route = `#/project/${encodeURIComponent(project)}`;
  if (note) {
    route += `/note/${encodeURIComponent(note)}`;
  }
  return route;
}

function decodeRoute(hash) {
  const prefix = "#/project/";
  if (!hash.startsWith(prefix)) {
    return null;
  }
  const remainder = hash.slice(prefix.length);
  const marker = "/note/";
  const markerIndex = remainder.indexOf(marker);
  const encodedProject = markerIndex >= 0 ? remainder.slice(0, markerIndex) : remainder;
  const encodedNote = markerIndex >= 0 ? remainder.slice(markerIndex + marker.length) : "";
  if (!encodedProject) {
    return null;
  }
  try {
    return {
      project: decodeURIComponent(encodedProject),
      note: encodedNote ? decodeURIComponent(encodedNote) : "",
    };
  } catch {
    return null;
  }
}

function sameRoute(left, right) {
  return Boolean(
    left
    && right
    && left.project === right.project
    && (left.note || "") === (right.note || ""),
  );
}

export default function(component) {
  const { data, parentElement, setStateValue } = component;
  const root = parentElement.querySelector(".la-workspace-navigation");
  if (!root || !data) {
    return;
  }

  const parentWindow = window.parent;
  const current = {
    project: data.project || "",
    note: data.note || "",
  };
  const pushRoute = data.pushRoute || null;

  const reportRoute = (route) => {
    if (route && !sameRoute(route, current)) {
      setStateValue("route", {
        project: route.project,
        note: route.note || "",
      });
    }
  };

  const setHash = (route, replace = false) => {
    const hash = encodeRoute(route.project, route.note);
    if (parentWindow.location.hash === hash) {
      return;
    }
    if (replace) {
      parentWindow.history.replaceState(null, "", hash);
    } else {
      parentWindow.location.hash = hash;
    }
  };

  if (data.mode === "projects") {
    if (pushRoute && pushRoute.project) {
      setHash(pushRoute);
    } else {
      const hashRoute = decodeRoute(parentWindow.location.hash);
      if (hashRoute) {
        reportRoute(hashRoute);
      } else if (current.project) {
        setHash(current, true);
      }
    }

    if (parentWindow.__laWorkspaceHashHandler) {
      parentWindow.removeEventListener("hashchange", parentWindow.__laWorkspaceHashHandler);
    }
    parentWindow.__laWorkspaceHashHandler = () => {
      const route = decodeRoute(parentWindow.location.hash);
      if (route) {
        reportRoute(route);
      }
    };
    parentWindow.addEventListener("hashchange", parentWindow.__laWorkspaceHashHandler);
  }

  root.replaceChildren();
  if (data.mode === "projects") {
    const label = document.createElement("label");
    label.className = "la-workspace-navigation-label";
    label.textContent = "Projects";

    const select = document.createElement("select");
    select.className = "la-workspace-project-select";
    select.setAttribute("aria-label", "Projects");
    for (const project of data.projects || []) {
      const option = document.createElement("option");
      option.value = project.name;
      option.textContent = project.label || project.name;
      option.selected = project.name === current.project;
      select.appendChild(option);
    }
    select.onchange = () => {
      const project = (data.projects || []).find((item) => item.name === select.value);
      const route = {
        project: select.value,
        note: project?.defaultNote || "",
      };
      setHash(route);
    };

    label.appendChild(select);
    root.appendChild(label);
    return;
  }

  root.setAttribute("role", "navigation");
  root.setAttribute("aria-label", "Notes");
  for (const note of data.notes || []) {
    const route = { project: current.project, note: note.name };
    const link = document.createElement("a");
    link.className = `la-workspace-note-link${note.name === current.note ? " is-selected" : ""}`;
    link.href = encodeRoute(route.project, route.note);
    link.setAttribute("aria-current", note.name === current.note ? "page" : "false");
    link.textContent = `${note.icon === "wrench" ? "🔧" : "▤"} ${note.label || note.name}`;
    link.onclick = (event) => {
      event.preventDefault();
      setHash(route);
    };
    root.appendChild(link);
  }
}
