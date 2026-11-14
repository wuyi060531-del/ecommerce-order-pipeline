/* Static demo: renders the data embedded from demo-data.json. No network access, no framework. */
(function () {
  "use strict";

  var node = document.getElementById("demo-data");
  if (!node) { return; }
  var D = JSON.parse(node.textContent);

  function h(tag, attrs) {
    var el = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      var v = attrs[k];
      if (v === false || v === null || v === undefined) { return; }
      if (k === "class") { el.className = v; }
      else if (k === "text") { el.textContent = v; }
      else if (k.slice(0, 2) === "on") { el.addEventListener(k.slice(2), v); }
      else { el.setAttribute(k, v === true ? "" : v); }
    });
    for (var i = 2; i < arguments.length; i++) { add(el, arguments[i]); }
    return el;
  }
  function add(el, c) {
    if (c === null || c === undefined || c === false) { return; }
    if (Array.isArray(c)) { c.forEach(function (x) { add(el, x); }); }
    else { el.appendChild(typeof c === "object" ? c : document.createTextNode(String(c))); }
  }
  function mount(id, content) {
    var el = document.getElementById(id);
    el.textContent = "";
    add(el, content);
    return el;
  }
  function yuan(cents) { return Math.floor(cents / 100) + "." + String(cents % 100).padStart(2, "0"); }
  function money(cents) { return yuan(cents) + " 元"; }
  function span(cls, text) { return h("span", { class: cls, text: text }); }

  var OUTCOME = { accepted: "接收", quarantined: "隔离", duplicate_removed: "重复副本" };
  var FIELD_OF_RULE = {
    invalid_quantity: ["quantity"], invalid_price: ["unit_price"], missing_city: ["city"],
    invalid_time: ["order_time"], invalid_status: ["status"], invalid_order_id: ["order_id"],
    invalid_user_id: ["user_id"], invalid_product_name: ["product_name"], invalid_city: ["city"]
  };
  var M = D.metrics;

  /* tabs with roving tabindex and arrow-key navigation */
  function tabs(listId, items, selected, onSelect) {
    var list = document.getElementById(listId);
    list.textContent = "";
    var buttons = items.map(function (item, i) {
      var b = h("button", {
        type: "button", role: "tab", class: "tab", id: listId + "-" + item.id, text: item.label,
        "aria-selected": i === selected ? "true" : "false", tabindex: i === selected ? "0" : "-1",
        "aria-controls": list.getAttribute("data-panel")
      });
      b.addEventListener("click", function () { choose(i, false); });
      b.addEventListener("keydown", function (e) {
        var n = items.length, to = null;
        if (e.key === "ArrowRight" || e.key === "ArrowDown") { to = (i + 1) % n; }
        else if (e.key === "ArrowLeft" || e.key === "ArrowUp") { to = (i + n - 1) % n; }
        else if (e.key === "Home") { to = 0; }
        else if (e.key === "End") { to = n - 1; }
        if (to !== null) { e.preventDefault(); choose(to, true); }
      });
      list.appendChild(b);
      return b;
    });
    function choose(i, focus) {
      buttons.forEach(function (b, j) {
        b.setAttribute("aria-selected", j === i ? "true" : "false");
        b.tabIndex = j === i ? 0 : -1;
      });
      if (focus) { buttons[i].focus(); }
      onSelect(items[i], buttons[i].id);
    }
    choose(selected, false);
  }

  /* ---------- hero ---------- */
  (function () {
    var rerun = D.scenarios.rerun.stats;
    var allMatch = D.reconciliation.checks.every(function (c) { return c.match; });
    function fact(big, small) { return h("div", { class: "fact" }, h("b", { text: big }), span("", small)); }
    mount("hero-facts", [
      fact(M.raw_rows + " 行 → " + M.accepted_orders + " 行入库", "隔离 " + M.quarantined_rows + " 行，重复副本 " + M.duplicate_rows + " 行，都有审计记录"),
      fact("paid " + money(M.sales_amount_cents), allMatch ? "SQL 聚合与独立重算逐项一致" : "对账存在不一致，见第 4 节"),
      fact("重跑写入 " + rerun.written + " 行", "同一文件再次加载：接收 " + rerun.incoming + "，未变化 " + rerun.unchanged)
    ]);
  })();

  /* ---------- 01 rows ---------- */
  var filter = "all", selected = null;
  var FILTERS = [
    { id: "all", label: "全部", test: function () { return true; } },
    { id: "accepted", label: "接收", test: function (r) { return r.outcome === "accepted"; } },
    { id: "quarantined", label: "隔离", test: function (r) { return r.outcome === "quarantined"; } },
    { id: "duplicate_removed", label: "重复副本", test: function (r) { return r.outcome === "duplicate_removed"; } },
    { id: "issues", label: "命中规则", test: function (r) { return r.issues.length > 0; } }
  ];
  function flagged(row) {
    var out = {};
    row.issues.forEach(function (i) {
      (FIELD_OF_RULE[i.rule] || (i.rule === "exact_duplicate" ? ["order_id"] : [])).forEach(function (f) { out[f] = true; });
    });
    return out;
  }
  function rawCell(row, field, flags, cls) {
    var v = row.raw[field];
    var content = v === "" ? span("empty", "（空）") : v;
    return h("td", { class: cls || "" }, flags[field] ? h("span", { class: "flag", text: v === "" ? "（空）" : v }) : content);
  }
  function renderFilters() {
    var box = document.getElementById("row-filters");
    box.textContent = "";
    FILTERS.forEach(function (f) {
      var n = D.rows.filter(f.test).length;
      box.appendChild(h("button", {
        type: "button", class: "chip", "aria-pressed": String(filter === f.id),
        onclick: function () { filter = f.id; renderFilters(); renderTable(true); }
      }, f.label, h("span", { class: "n", text: String(n) })));
    });
  }
  function renderTable(announce) {
    var f = FILTERS.filter(function (x) { return x.id === filter; })[0];
    var rows = D.rows.filter(f.test);
    var head = h("thead", {}, h("tr", {},
      h("th", { scope: "col", text: "记录" }), h("th", { scope: "col", text: "订单号" }),
      h("th", { scope: "col", class: "col-opt", text: "商品" }), h("th", { scope: "col", class: "num-cell", text: "数量" }),
      h("th", { scope: "col", class: "num-cell", text: "单价" }), h("th", { scope: "col", class: "col-opt", text: "时间" }),
      h("th", { scope: "col", class: "col-opt", text: "城市" }), h("th", { scope: "col", text: "状态" }),
      h("th", { scope: "col", text: "结果与规则" })));
    var body = h("tbody", {});
    rows.forEach(function (r) {
      var fl = flagged(r), on = selected === r.record;
      body.appendChild(h("tr", { class: "out-" + r.outcome + (on ? " is-selected" : "") },
        h("td", { class: "mono", text: String(r.record) }),
        h("td", {}, h("button", {
          type: "button", class: "row-btn", "aria-current": on ? "true" : false,
          "aria-label": r.order_id + "（记录 " + r.record + "，" + OUTCOME[r.outcome] + "），查看详情",
          onclick: function () { selected = r.record; renderTable(false); renderDetail(); }
        }, r.order_id)),
        rawCell(r, "product_name", fl, "col-opt mono"),
        rawCell(r, "quantity", fl, "num-cell"), rawCell(r, "unit_price", fl, "num-cell"),
        rawCell(r, "order_time", fl, "col-opt mono"), rawCell(r, "city", fl, "col-opt mono"),
        rawCell(r, "status", fl, "mono"),
        h("td", {}, h("div", { class: "rules-cell" },
          span("badge " + r.outcome, OUTCOME[r.outcome]),
          r.issues.map(function (i) { return span("rule " + i.severity, i.rule); })))));
    });
    var caption = h("caption", { class: "sr-only", text: "原始 CSV 记录；带虚线下划线的单元格触发了规则" });
    mount("row-table", [caption, head, body]);
    document.getElementById("row-status").textContent = announce ? "显示 " + rows.length + " 条记录" : "";
  }
  function renderDetail() {
    var r = D.rows.filter(function (x) { return x.record === selected; })[0];
    if (!r) { mount("row-detail", null); return; }
    var fl = flagged(r), c = r.clean;
    var raw = h("dl", { class: "kv" }, Object.keys(r.raw).map(function (k) {
      return [h("dt", { text: k }), h("dd", {}, fl[k] ? h("span", { class: "flag", text: r.raw[k] === "" ? "（空）" : r.raw[k] }) : (r.raw[k] === "" ? "（空）" : r.raw[k]))];
    }));
    var clean = c ? h("dl", { class: "kv" },
      h("dt", { text: "city" }), h("dd", { text: c.city }),
      h("dt", { text: "status" }), h("dd", { text: c.status }),
      h("dt", { text: "quantity" }), h("dd", { text: String(c.quantity) }),
      h("dt", { text: "unit_price_cents" }), h("dd", { text: c.unit_price_cents + "（" + yuan(c.unit_price_cents) + " 元）" }),
      h("dt", { text: "order_amount_cents" }), h("dd", { text: c.order_amount_cents + "（" + yuan(c.order_amount_cents) + " 元）" }),
      h("dt", { text: "order_time" }), h("dd", { text: c.order_time === null ? "NULL（无法解析，留空）" : c.order_time })
    ) : h("p", { class: "muted", text: r.outcome === "duplicate_removed"
      ? "未入库：与记录 " + r.duplicate_of + " 完全相同，只保留第一条。"
      : "未入库：整行被隔离，原始值保留在审计记录里。" });
    var reasons = r.issues.length ? h("ul", { class: "reason" }, r.issues.map(function (i) {
      return h("li", {}, span("rule " + i.severity, i.rule), " ", span("muted", i.severity + " · " + i.action),
        h("p", { text: i.message }));
    })) : h("p", { class: "muted", text: "没有命中任何规则，原样接收。" });
    mount("row-detail", [
      h("div", { class: "card" }, h("h4", { text: "原始（CSV 原样）· 记录 " + r.record }), raw),
      h("div", { class: "card" }, h("h4", {}, "处理结果：", span("badge " + r.outcome, OUTCOME[r.outcome])), clean),
      h("div", { class: "card wide" }, h("h4", { text: "命中的规则" }), reasons)
    ]);
  }
  selected = (D.rows.filter(function (r) { return r.issues.length; })[0] || D.rows[0]).record;
  renderFilters(); renderTable(false); renderDetail();

  /* ---------- 02 counts ---------- */
  (function () {
    function tile(label, value, sub, hl) {
      return h("div", { class: "tile" + (hl ? " hl" : "") }, h("div", { class: "label", text: label }),
        h("div", { class: "value", text: String(value) }), sub ? h("div", { class: "sub", text: sub }) : null);
    }
    mount("metric-tiles", [
      tile("原始行", M.raw_rows), tile("接收", M.accepted_orders), tile("隔离", M.quarantined_rows),
      tile("重复副本", M.duplicate_rows), tile("paid 订单", M.paid_orders),
      tile("paid 销售额", yuan(M.sales_amount_cents) + " 元", M.sales_amount_cents + " 分", true),
      tile("含异常原始行", M.anomalous_rows), tile("含警告原始行", M.warning_rows)
    ]);
    var parts = [["accepted", M.accepted_orders], ["quarantined", M.quarantined_rows], ["duplicate_removed", M.duplicate_rows]];
    var segs = parts.map(function (p) { var i = h("i", { class: p[0] }); i.style.flexGrow = String(p[1]); i.style.flexBasis = "0"; return i; });
    mount("split-bar", [
      h("p", { class: "equation", text: M.raw_rows + " = " + M.accepted_orders + "（接收） + " + M.quarantined_rows + "（隔离） + " + M.duplicate_rows + "（重复副本）" }),
      h("div", { class: "split", role: "img", "aria-label": "原始行构成：接收 " + M.accepted_orders + "，隔离 " + M.quarantined_rows + "，重复副本 " + M.duplicate_rows }, segs),
      h("ul", { class: "legend" }, parts.map(function (p) { return h("li", {}, span("sw " + p[0]), OUTCOME[p[0]] + " " + p[1]); }))
    ]);
    var head = h("thead", {}, h("tr", {}, ["规则 rule", "级别", "动作", "命中", "记录"].map(function (t) { return h("th", { scope: "col", text: t }); })));
    var body = h("tbody", {}, D.rules.map(function (r) {
      return h("tr", {}, h("td", {}, span("rule " + r.severity, r.rule)), h("td", { class: "mono", text: r.severity }),
        h("td", { class: "mono", text: r.action }), h("td", { class: "num-cell", text: String(r.count) }),
        h("td", { class: "mono", text: r.records.join(", ") }));
    }));
    mount("rule-table", [h("caption", { class: "sr-only", text: "每条规则的命中次数；记录号与表头同样计数" }), head, body]);
  })();

  /* ---------- 03 stages ---------- */
  (function () {
    var S = D.stages;
    function li(label, value) { return h("li", {}, label + " ", h("b", { text: String(value) })); }
    var stages = [
      { t: "读取 CSV", w: "quality.read_csv", d: "只读打开；全部按文本读取，保留前导零、避免浮点；检查表头和字段数。",
        o: [li("记录", S.read.records), li("字段", S.read.fields), li("源文件 SHA-256", D.meta.source_sha256.slice(0, 12) + "…")] },
      { t: "校验与隔离", w: "quality.inspect_csv", d: "逐行套用规则：坏行隔离并写审计记录；完全相同的重复行只留第一条。",
        o: [li("接收", S.validate.accepted), li("隔离", S.validate.quarantined), li("重复副本", S.validate.duplicates), li("审计记录", S.validate.issues + " 条")] },
      { t: "规范化", w: "quality.inspect_csv + transforms", d: "单价和数量用 Decimal 算出整数分；城市为空补“未知”；日期无效留空，订单仍然保留。",
        o: [li("城市补“未知”", S.normalise.missing_city + " 行"), li("日期留空", S.normalise.invalid_time + " 行"), li("paid 订单", S.normalise.paid_orders + " 笔"), li("paid 金额", S.normalise.sales_amount_cents + " 分")] },
      { t: "单事务写入", w: "storage.SQLiteStore.load", tx: true, d: "orders、etl_runs、dq_issues 在同一事务里提交；只写有变化的行；任何一步失败整批回滚。",
        o: [li("接收 / 写入 / 未变化", S.load.stats.incoming + " / " + S.load.stats.written + " / " + S.load.stats.unchanged),
            li("etl_runs", S.load.tables.etl_runs + " 行"), li("dq_issues", S.load.tables.dq_issues + " 行")] },
      { t: "SQL 分析", w: "sql/analysis_mysql.sql", d: "对全库已提交订单做 paid 聚合：总览、城市排行、商品排行。",
        o: [li("总订单", S.analyze.total_orders), li("paid", S.analyze.paid_orders + " 笔 · " + S.analyze.sales_amount_cents + " 分"), li("城市 / 商品分组", S.analyze.city_rows + " / " + S.analyze.product_rows)] },
      { t: "报告与发布", w: "pipeline._report / _publish", d: "生成明细、统计、manifest 和 Markdown 报告，再整体替换输出目录。",
        o: [li("产物文件", S.report.files.length + " 个"), h("li", {}, h("div", { class: "files" }, S.report.files.map(function (f) { return span("rule", f); })))] }
    ];
    mount("stage-list", stages.map(function (s) {
      return h("li", { class: "stage" + (s.tx ? " tx" : "") },
        h("h3", {}, s.t, s.tx ? span("tag", "单事务") : null), h("p", { class: "where", text: s.w }), h("p", { text: s.d }), h("ul", {}, s.o));
    }));
  })();

  /* ---------- 04 reconciliation ---------- */
  (function () {
    document.getElementById("sql-text").textContent = D.sql.summary_query;
    var s = D.sql.summary;
    mount("sql-result", [
      h("thead", {}, h("tr", {}, ["total_orders", "paid_orders", "sales_amount_cents"].map(function (t) { return h("th", { scope: "col", class: "num-cell", text: t }); }))),
      h("tbody", {}, h("tr", {}, h("td", { class: "num-cell", text: String(s.total_orders) }), h("td", { class: "num-cell", text: String(s.paid_orders) }),
        h("td", { class: "num-cell", text: s.sales_amount_cents + "（" + yuan(s.sales_amount_cents) + " 元）" })))
    ]);
    mount("check-list", D.reconciliation.checks.map(function (c) {
      var max = Math.max(c.sql, c.other) || 1;
      function row(label, v, cls) {
        var bar = h("div", { class: "bar " + cls }, h("b", {}));
        bar.firstChild.style.setProperty("--w", (100 * v / max) + "%");
        return h("div", { class: "row" }, span("", label), h("span", { class: "val", text: String(v) }), bar);
      }
      return h("li", { class: "check" },
        h("header", {}, h("span", { class: "name", text: c.label }), span("badge " + (c.match ? "ok" : "bad"), c.match ? "✓ 一致" : "✗ 不一致")),
        h("div", { class: "pair" }, row(c.left_label, c.sql, "sql"), row(c.other_label, c.other, "other")));
    }));
    var groups = [{ id: "city", label: "按城市", rows: D.reconciliation.by_city, col: "city" },
                  { id: "product", label: "按商品", rows: D.reconciliation.by_product, col: "product_name" }];
    document.getElementById("group-tabs").setAttribute("data-panel", "group-panel");
    tabs("group-tabs", groups, 0, function (g, tabId) {
      var panel = document.getElementById("group-panel");
      panel.setAttribute("aria-labelledby", tabId);
      var total = g.rows.reduce(function (a, r) { return a + r.sql_cents; }, 0);
      mount("group-panel", h("div", { class: "table-wrap panel-in" }, h("table", { class: "grid" },
        h("caption", { class: "sr-only", text: g.label + "：SQL 与 Pandas 批次汇总的销售额（分）" }),
        h("thead", {}, h("tr", {}, [g.col, "SQL（分）", "Pandas（分）", "核对"].map(function (t, i) { return h("th", { scope: "col", class: i && i < 3 ? "num-cell" : "", text: t }); }))),
        h("tbody", {}, g.rows.map(function (r) {
          return h("tr", {}, h("td", { class: "mono", text: r.name }), h("td", { class: "num-cell", text: String(r.sql_cents) }),
            h("td", { class: "num-cell", text: String(r.pandas_cents) }), h("td", {}, span("badge " + (r.match ? "ok" : "bad"), r.match ? "✓ 一致" : "✗ 不一致")));
        })),
        h("tfoot", {}, h("tr", {}, h("th", { scope: "row", text: "合计" }), h("td", { class: "num-cell", text: String(total) }),
          h("td", { class: "num-cell", text: String(total) }), h("td", {}))))));
    });
  })();

  /* ---------- 05 scenarios ---------- */
  var SC = D.scenarios;
  function stateCard(title, s, other) {
    function line(label, key, fmt) {
      var changed = other && other[key] !== s[key];
      return [h("dt", { text: label }), h("dd", { class: changed ? "chg" : "same" }, (fmt ? fmt(s[key]) : String(s[key])) + (changed ? "（变化）" : ""))];
    }
    return h("div", { class: "card state" }, h("h4", { text: title }),
      h("dl", { class: "kv" },
        line("orders 行数", "orders"), line("etl_runs 行数", "etl_runs"), line("dq_issues 行数", "dq_issues"),
        line("paid 订单", "paid_orders"), line("paid 销售额", "paid_sales_cents", function (v) { return v + " 分（" + yuan(v) + " 元）"; }),
        line("表内容摘要", "digest", function (v) { return v === null ? "（库尚不存在）" : v; })));
  }
  function statsLine(s) {
    return h("p", { class: "logline", text: "接收 " + s.incoming + "，写入 " + s.written + "，未变化 " + s.unchanged +
      "；批次元数据写入 " + s.run_written + "，问题记录写入 " + s.issues_written });
  }
  function writesList(writes) {
    if (!writes.length) { return h("p", { class: "logline", text: "事务内观察到的写操作：0 次" }); }
    return h("ul", { class: "write-list" }, writes.map(function (w) { return h("li", { text: w.table + " " + w.op + " × " + w.count }); }));
  }
  function totalWrites(writes) { return writes.reduce(function (a, w) { return a + w.count; }, 0); }

  function simple(title, text, sc, extra) {
    return h("div", { class: "panel-in" },
      h("p", { class: "section-lede", text: text }),
      h("div", { class: "state-pair" }, stateCard("之前：已提交的库", sc.before), h("div", { class: "arrow", "aria-hidden": "true", text: "→" }), stateCard("之后", sc.after, sc.before)),
      statsLine(sc.stats),
      h("h4", { text: "事务内观察到的写操作（共 " + totalWrites(sc.writes) + " 次）" }), writesList(sc.writes), extra);
  }

  function failurePanel() {
    var f = SC.failure, step = 0;
    var steps = [{ t: "数据库当前状态", d: "已提交的库里有第一批的 " + f.before.orders + " 笔订单。", kind: "start" },
                 { t: "事务开始", d: "BEGIN IMMEDIATE：之后的写入都在同一个事务里，别的连接看不到。", kind: "begin" }];
    f.writes_before_failure.forEach(function (w) {
      steps.push({ t: w.table + " " + w.op + " " + w.key + (w.detail ? "（" + w.detail.replace("->", " → ") + "）" : ""), d: "事务内已写入，尚未提交。", kind: "write", w: w });
    });
    steps.push({ t: "写入 " + f.injected.order_id + " 失败", d: f.injected.field + " = " + f.injected.value + " 违反 " + f.injected.constraint + "；数据库报错：" + f.error, kind: "fail" });
    steps.push({ t: "回滚", d: "事务内的全部写入一并撤销；已提交的库回到开始前的样子。", kind: "rollback" });
    var wrap = h("div", { class: "panel-in" });
    var live = h("p", { class: "sr-only", "aria-live": "polite" });
    var diffRows = f.diff.map(function (d) {
      var what = d.kind === "insert" ? "新增：" + d.values.status + "，" + d.values.order_amount_cents + " 分"
        : Object.keys(d.changes).map(function (k) { return k + " " + d.changes[k][0] + " → " + d.changes[k][1]; }).join("；");
      var inj = d.order_id === f.injected.order_id;
      return h("tr", {}, h("td", { class: "mono", text: d.order_id }), h("td", { class: "mono", text: what }),
        h("td", {}, inj ? span("badge bad", "注入的坏值") : span("muted", d.kind === "insert" ? "新订单" : "正常变化")));
    });
    function draw() {
      var s = steps[step], inTx = steps.slice(2, step + 1).filter(function (x) { return x.kind === "write"; });
      var failed = step >= steps.length - 2, rolled = step === steps.length - 1;
      var items = steps.map(function (x, i) {
        return h("li", { class: (i < step ? "done " : "") + (i === step ? "now " : "") + (x.kind === "fail" ? "fail" : ""), "aria-current": i === step ? "step" : false },
          h("span", { class: "dot", "aria-hidden": "true", text: String(i + 1) }), h("div", {}, h("strong", { text: x.t }), h("div", { class: "muted", text: x.d })));
      });
      var txBox = step >= 1 ? h("div", { class: "txbox" + (rolled ? " rolled" : "") }, h("h4", { text: rolled ? "事务（已回滚，内容作废）" : "事务（未提交）" }),
        inTx.length ? h("ul", { class: "write-list" }, inTx.map(function (x) { return h("li", { text: x.t }); })) : h("p", { class: "muted", text: "还没有写入" }),
        failed && !rolled ? h("p", { class: "write-list", text: "✗ " + f.injected.order_id + "：" + f.injected.constraint + " 失败" }) : null) : null;
      var committed = stateCard("已提交的库" + (rolled ? "（回滚后）" : "（对外可见）"), rolled ? f.after : f.before, rolled ? f.before : null);
      mount("fail-body", [
        h("div", { class: "controls" },
          h("button", { type: "button", class: "btn", id: "fail-prev", disabled: step === 0, onclick: function () { go(step - 1); } }, "上一步"),
          h("button", { type: "button", class: "btn primary", id: "fail-next", disabled: step === steps.length - 1, onclick: function () { go(step + 1); } }, "下一步"),
          h("button", { type: "button", class: "btn", id: "fail-reset", disabled: step === 0, onclick: function () { go(0); } }, "重置")),
        h("div", { class: "fail-grid" }, h("ol", { class: "stepper" }, items), h("div", {}, txBox, committed)),
        rolled ? h("p", { class: "logline", text: "回滚后摘要 " + f.after.digest + (f.rolled_back ? " = " : " ≠ ") + "回滚前摘要 " + f.before.digest +
          "；SYN001 状态仍是 " + f.syn001_status_after + "；etl_runs 仍是 " + f.after.etl_runs + " 行" }) : null
      ]);
      live.textContent = "第 " + (step + 1) + " 步，共 " + steps.length + " 步：" + s.t + "。" + s.d;
    }
    function go(n) {
      var keep = document.activeElement && document.activeElement.id;
      step = Math.max(0, Math.min(steps.length - 1, n)); draw();
      var again = keep && document.getElementById(keep);
      if (!again || again.disabled) { again = document.querySelector("#fail-body .btn:not(:disabled)"); }
      if (again) { again.focus(); }
    }
    wrap.appendChild(h("p", { class: "section-lede", text: "第二批文件（" + f.batch.name + "，" + f.batch.raw_rows + " 行）改了 SYN001 的状态、新增了 SYN030，并故意让 SYN002 的数量为 0 来触发数据库约束（注入方式与 tests/v1/test_storage.py 相同）。" }));
    wrap.appendChild(h("div", { class: "table-wrap", role: "region", "aria-label": "第二批与库的差异", tabindex: "0" }, h("table", { class: "grid" },
      h("caption", { class: "sr-only", text: "第二批与已提交数据的差异" }),
      h("thead", {}, h("tr", {}, ["订单", "变化", "说明"].map(function (t) { return h("th", { scope: "col", text: t }); }))), h("tbody", {}, diffRows))));
    wrap.appendChild(h("h4", { text: "逐步回放" }));
    wrap.appendChild(h("div", { id: "fail-body" }));
    wrap.appendChild(live);
    return { el: wrap, init: draw };
  }

  var SCENES = [
    { id: "first", label: "① 首次加载" }, { id: "rerun", label: "② 同一文件重跑" },
    { id: "fail", label: "③ 第二批写入失败" }, { id: "recover", label: "④ 修正后重跑" }
  ];
  document.getElementById("tx-tabs").setAttribute("data-panel", "tx-panel");
  tabs("tx-tabs", SCENES, 0, function (sc, tabId) {
    var panel = document.getElementById("tx-panel");
    panel.setAttribute("aria-labelledby", tabId);
    if (sc.id === "first") {
      mount("tx-panel", simple("", "空库里加载 data/raw/orders.csv：26 笔订单、1 个批次、7 条质量问题记录在同一个事务里提交。", SC.first_load));
    } else if (sc.id === "rerun") {
      var r = SC.rerun;
      mount("tx-panel", simple("", "同一个文件再跑一次：比较库里的当前值，没变的行不写。" , r,
        h("p", { class: "note", text: "数据库状态" + (r.state_identical ? "前后完全相同" : "发生了变化") + "；输出文件" + (r.artifacts_identical ? "逐字节相同" : "有差异") + "。" })));
    } else if (sc.id === "fail") {
      var p = failurePanel(); mount("tx-panel", p.el); p.init();
    } else {
      var c = SC.recovery;
      mount("tx-panel", simple("", "把第二批里的坏值改正后重跑：只有真正变化的行被写入（SYN001 更新、SYN030 新增），其余保持不变。", c,
        h("p", { class: "note", text: "SYN001 现在是 " + c.syn001_status_after + "；paid 订单 " + c.after.paid_orders + " 笔，销售额 " + money(c.after.paid_sales_cents) + "。" })));
    }
  });

  document.getElementById("meta-line").textContent = "rule " + D.meta.rule_version + " · app " + D.meta.app_version + " · " +
    D.meta.backend + " · " + D.meta.source_file + " · sha256 " + D.meta.source_sha256.slice(0, 12) + " · run " + D.meta.run_id.slice(0, 12);
})();
