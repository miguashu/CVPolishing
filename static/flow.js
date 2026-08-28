// -*- coding: utf-8 -*-
// 流程可视化页：初始化 mermaid 并渲染流程图 / 时序图

if (window.mermaid) {
  window.mermaid.initialize({
    startOnLoad: true,
    theme: "dark",
    securityLevel: "loose",
    flowchart: { curve: "basis", htmlLabels: true },
    sequence: { useMaxWidth: true, showSequenceNumbers: false },
  });
}
