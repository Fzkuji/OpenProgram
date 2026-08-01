from collections import deque


def _nodes(graph):
    s = set(graph)
    for vs in graph.values():
        s.update(vs)
    return s


def topo_sort(graph):
    nodes = _nodes(graph)
    indeg = {n: 0 for n in nodes}
    for u in graph:
        for v in graph[u]:
            indeg[v] += 1
    ready = sorted(n for n in nodes if indeg[n] == 0)
    out = []
    while ready:
        n = ready.pop(0)
        out.append(n)
        for v in sorted(graph.get(n, [])):
            indeg[v] -= 1
            if indeg[v] == 0:
                ready.append(v)
        ready.sort()
    if len(out) != len(nodes):
        raise ValueError("cycle")
    return out


def shortest_path(graph, a, b):
    if a == b:
        return [a]
    seen, q = {a}, deque([[a]])
    while q:
        path = q.popleft()
        for v in sorted(graph.get(path[-1], [])):
            if v in seen:
                continue
            if v == b:
                return path + [v]
            seen.add(v)
            q.append(path + [v])
    return None


def components(graph):
    adj = {n: set() for n in _nodes(graph)}
    for u in graph:
        for v in graph[u]:
            adj[u].add(v)
            adj[v].add(u)
    seen, out = set(), []
    for n in sorted(adj):
        if n in seen:
            continue
        comp, stack = [], [n]
        seen.add(n)
        while stack:
            x = stack.pop()
            comp.append(x)
            for y in adj[x]:
                if y not in seen:
                    seen.add(y)
                    stack.append(y)
        out.append(sorted(comp))
    return sorted(out, key=lambda c: c[0])
