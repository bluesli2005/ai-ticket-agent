'use strict';
const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
const esc = (s) =>
  String(s ?? '').replace(
    /[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c],
  );
const paths = {
  dashboard: 'M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z',
  tickets: 'M5 3h14v18l-3-2-4 2-4-2-3 2z M8 8h8 M8 12h6',
  book: 'M4 3h14a2 2 0 0 1 2 2v16H6a3 3 0 0 1-3-3V6a3 3 0 0 1 3-3 M3 17h17 M8 7h7 M8 10h5',
  settings:
    'M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8 M12 2v3 M12 19v3 M2 12h3 M19 12h3 M5 5l2 2 M17 17l2 2 M5 19l2-2 M17 7l2-2',
  plus: 'M12 5v14 M5 12h14',
  arrow: 'M5 12h14 M14 7l5 5-5 5',
  back: 'M19 12H5 M10 7l-5 5 5 5',
  search: 'M10 3a7 7 0 1 0 0 14 7 7 0 0 0 0-14 M15 15l6 6',
  upload: 'M12 16V3 M7 8l5-5 5 5 M4 15v5h16v-5',
  spark: 'M12 2l3 7 7 3-7 3-3 7-3-7-7-3 7-3z',
  clock: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18 M12 7v5l3 2',
  check: 'M5 12l4 4L19 6',
  shield: 'M12 3l8 3v6c0 5-8 9-8 9s-8-4-8-9V6z M8 12l3 3 5-6',
  download: 'M12 3v13 M7 11l5 5 5-5 M4 17v4h16v-4',
  refresh: 'M20 7a9 9 0 1 0 1 9 M20 2v6h-6',
  close: 'M5 5l14 14 M19 5L5 19',
  file: 'M6 3h8l4 4v14H6z M14 3v5h4 M9 12h6 M9 16h6',
};
const icon = (name) =>
  `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${paths[name] || paths.file}"/></svg>`;
let config,
  currentTicket,
  currentDoc,
  routeEpoch = 0,
  modelState = null,
  toastTimer,
  docFilter = 'all';
const busyTickets = new Set();
const dateTime = (s) =>
  s
    ? new Intl.DateTimeFormat('zh-CN', {
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
      }).format(new Date(s))
    : '—';
const fullDate = (s) => new Date(s).toLocaleString('zh-CN');
const btn = (text, action, cls = '', id = '') =>
  `<button type="button" class="btn ${cls}" data-action="${action}" ${id ? `data-id="${id}"` : ''}>${text}</button>`;
const badge = (text, color = '') => `<span class="tag ${color}">${esc(text)}</span>`;
const statusBadge = (s) =>
  badge(s, { 待处理: 'amber', 处理中: 'blue', 已解决: 'green', 已关闭: '' }[s]);
const priority = (p) =>
  `<span class="priority ${{ 高: 'high', 紧急: 'urgent', 低: 'low' }[p] || ''}"><i></i>${esc(p)}</span>`;
const sample = (s) => (s ? '<span class="tag sample">示例</span>' : '');
const options = (values, selected, empty = '') =>
  (empty ? `<option value="">${empty}</option>` : '') +
  values
    .map((v) => `<option value="${esc(v)}" ${v === selected ? 'selected' : ''}>${esc(v)}</option>`)
    .join('');
const docStatus = (d) =>
  badge(
    !d.approved
      ? '待审核'
      : {
          ready: '语义检索就绪',
          lexical: '仅关键词',
          failed: '导入失败',
          queued: '等待处理',
          parsing: '解析中',
          indexing: '建立索引中',
        }[d.status] || d.status,
    !d.approved
      ? 'purple'
      : { ready: 'green', lexical: 'amber', failed: 'red', indexing: 'blue', parsing: 'blue' }[
          d.status
        ] || '',
  );
const empty = (title, body, action = '', symbol = 'tickets') =>
  `<div class="empty">${icon(symbol)}<h3>${title}</h3><p>${body}</p>${action}</div>`;
const head = (title, sub, actions = '', eyebrow = '') =>
  `<div class="page-head"><div>${eyebrow ? `<div class="eyebrow">${eyebrow}</div>` : ''}<h1>${esc(title)}</h1><p class="subtitle">${sub}</p></div><div class="actions">${actions}</div></div>`;
function toast(message, error = false) {
  clearTimeout(toastTimer);
  $('#toast').textContent = message;
  $('#toast').className = 'visible' + (error ? ' error' : '');
  toastTimer = setTimeout(() => ($('#toast').className = ''), 5000);
}
async function api(path, data, method) {
  const response = await fetch('/api' + path, {
    method: method || (data ? 'POST' : 'GET'),
    headers: data ? { 'Content-Type': 'application/json', 'X-Desk-Token': config?.csrf || '' } : {},
    body: data ? JSON.stringify(data) : undefined,
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || '操作未完成');
  return result;
}
async function jobWait(id, onTick) {
  for (let i = 0; i < 600; i++) {
    const job = await api('/jobs/' + id);
    if (job.status === 'done') return job.result;
    if (job.status === 'failed') throw new Error(job.error || '后台任务失败');
    if (onTick) onTick(job);
    await new Promise((r) => setTimeout(r, 1500));
  }
  throw new Error('任务仍在运行，可稍后刷新查看结果');
}
function modal(title, body, footer = '', form = '') {
  $('#modal-content').innerHTML =
    `${form ? `<form id="${form}">` : ''}<div class="modal-head"><h2>${title}</h2><button type="button" class="close-btn" data-action="close-modal" aria-label="关闭">×</button></div><div class="modal-body"><div id="modal-error" role="alert"></div>${body}</div>${footer ? `<div class="modal-foot">${footer}</div>` : ''}${form ? '</form>' : ''}`;
  $('#modal').showModal();
}
const formFooter = (label) =>
  `${btn('取消', 'close-modal')}<button class="btn primary" type="submit">${label}</button>`;
function confirmAction(title, body, callback, label = '确认') {
  modal(
    title,
    `<p class="settings-note">${body}</p>`,
    `${btn('取消', 'close-modal')}<button id="confirm-go" class="btn danger">${label}</button>`,
  );
  $('#confirm-go').onclick = async () => {
    const b = $('#confirm-go');
    b.disabled = true;
    try {
      await callback();
      $('#modal').close();
    } catch (e) {
      $('#modal-error').className = 'modal-error';
      $('#modal-error').textContent = e.message;
      b.disabled = false;
    }
  };
}
function ticketTable(rows) {
  if (!rows.length)
    return empty(
      '这里还没有工单',
      '创建一条工单，让问题和处理过程都有记录。',
      btn(icon('plus') + ' 新建工单', 'new-ticket', 'primary'),
    );
  return `<div class="table-wrap"><table><thead><tr><th>工单</th><th>状态</th><th>优先级</th><th>类别</th><th>更新时间</th></tr></thead><tbody>${rows.map((t) => `<tr><td><a class="ticket-title" href="#ticket/${t.id}">${esc(t.title)}</a><div class="ticket-meta"><span>#${String(t.id).padStart(4, '0')}</span><span>${esc(t.requester || '未填写提交人')}</span>${sample(t.sample)}</div></td><td>${statusBadge(t.status)}</td><td>${priority(t.priority)}</td><td><span class="muted">${esc(t.category)}</span></td><td class="tiny muted nowrap">${dateTime(t.updated_at)}</td></tr>`).join('')}</tbody></table></div>`;
}
async function dashboard(epoch) {
  const [stats, tickets, docs] = await Promise.all([
    api('/stats'),
    api('/tickets'),
    api('/documents'),
  ]);
  if (epoch !== routeEpoch) return;
  const active = tickets.filter((t) => ['待处理', '处理中'].includes(t.status)).slice(0, 5);
  $('#main').innerHTML =
    head(
      '今天，也把问题处理妥当。',
      `还有 ${stats.statuses['待处理']} 条工单等待首次响应，用本地知识找到有依据的答案。`,
      btn(icon('upload') + ' 导入知识', 'new-document') +
        btn(icon('plus') + ' 新建工单', 'new-ticket', 'primary'),
      'WORKSPACE / 工作台',
    ) +
    (!stats.total && !docs.length
      ? `<div class="onboard"><div><h3>从一条工单开始</h3><p>可以导入自己的资料，也可以用示例工单体验完整处理流程。</p></div>${btn('载入示例数据', 'seed', 'soft')}</div>`
      : '') +
    `<div class="stats">${[
      ['待处理工单', stats.statuses['待处理'], '等待首次响应', 'tickets'],
      ['处理中', stats.statuses['处理中'], '正在推进的事项', 'clock'],
      ['已解决', stats.statuses['已解决'] + stats.statuses['已关闭'], '包含已关闭工单', 'check'],
      [
        '知识文档',
        stats.documents,
        `${stats.ready_documents} 份语义索引就绪 · ${stats.review_documents} 份待审核`,
        'book',
      ],
    ]
      .map(
        ([label, value, detail, i]) =>
          `<div class="stat"><div class="stat-label">${label}${icon(i)}</div><div class="stat-number">${value.toString().padStart(2, '0')}</div><div class="stat-detail">${detail}</div></div>`,
      )
      .join('')}</div>
 <div class="dashboard-layout"><section class="panel"><div class="panel-head"><div><h2>需要关注的工单 <span class="count-chip">${stats.statuses['待处理'] + stats.statuses['处理中']}</span></h2><p>先解决问题，再把经验留下来</p></div><a class="subtle-link" href="#tickets">查看全部 →</a></div>${ticketTable(active)}</section>
 <div class="two-col"><section class="panel assistant-intro"><div class="ai-orb">${icon('spark')}</div><h2>让知识，帮你多走一步。</h2><p>检索本地资料，整理带原文引用的处理建议。每一份草稿，都由你最终确认。</p><a class="subtle-link" href="#knowledge">检索知识库 →</a></section><section class="panel"><div class="panel-head"><h2>知识库</h2><a href="#knowledge" class="subtle-link">管理文档 →</a></div><div class="panel-body">${
   docs.length
     ? docs
         .slice(0, 4)
         .map(
           (d) =>
             `<div class="knowledge-row"><div class="file-icon">${d.filename.endsWith('.pdf') ? 'PDF' : 'DOC'}</div><div class="text"><a href="#document/${d.id}">${esc(d.title)}</a><p>${d.chunks} 个片段 · ${dateTime(d.updated_at)} ${d.sample ? '· 示例' : ''}</p></div>${docStatus(d)}</div>`,
         )
         .join('')
     : empty(
         '让 AI 了解你的业务',
         '导入操作手册、常见问题或已确认的解决方案。',
         btn('导入第一份文档', 'new-document', 'soft'),
         'book',
       )
 }</div></section>
 <section class="panel"><div class="panel-head"><h2>问题分布</h2><span class="tiny muted">全部 ${stats.total} 条</span></div><div class="panel-body">${stats.categories.length ? stats.categories.map((c) => `<div class="bar-row"><div class="bar-caption"><span>${esc(c.name)}</span><span>${c.count}</span></div><div class="bar"><span style="width:${Math.max(3, (c.count / Math.max(1, stats.total)) * 100)}%"></span></div></div>`).join('') : '<p class="settings-note">创建工单后，这里会显示问题类别分布。</p>'}<div class="insight">${icon('shield')}<span>回复有来源，处理有记录。AI 草稿不会自动发送，也不会自动关闭工单。</span></div></div></section></div></div>`;
}
async function ticketsView(epoch) {
  const params = new URLSearchParams(location.hash.split('?')[1] || '');
  const rows = await api('/tickets?' + params);
  if (epoch !== routeEpoch) return;
  const filtered = ['q', 'status', 'category'].some((key) => params.get(key));
  const exportUrl = '/api/tickets/export?' + params.toString();
  const resetLink = '<a class="btn ghost" href="#tickets">清除筛选</a>';
  const results =
    !rows.length && filtered
      ? empty('没有匹配的工单', '试试其他关键词，或清除筛选条件。', resetLink)
      : ticketTable(rows);
  $('#main').innerHTML =
    head(
      '工单',
      '跟踪每个问题，从提交到解决。',
      `<a class="btn" href="${esc(exportUrl)}" download="desk-tickets.csv">${icon('download')} 导出筛选结果</a>` +
        btn(icon('plus') + ' 新建工单', 'new-ticket', 'primary'),
    ) +
    `<section class="panel"><form id="ticket-filter" class="toolbar"><div class="search-field">${icon('search')}<input aria-label="搜索工单" name="q" value="${esc(params.get('q') || '')}" placeholder="搜索标题、描述或工单编号"></div><select name="status" aria-label="按状态筛选">${options(config.statuses, params.get('status'), '全部状态')}</select><select name="category" aria-label="按类别筛选">${options(config.categories, params.get('category'), '全部类别')}</select><button class="btn" type="submit">筛选</button>${filtered ? resetLink : ''}</form>${results}<div class="page-count">显示 ${rows.length} 条工单（页面最多显示500条；导出包含全部已应用筛选结果）</div><p class="settings-note panel-body">导出以已应用的筛选条件为准。CSV 包含问题描述、回复和解决方案；公式型文本会加单引号作为文本导出。</p></section>`;
}
function newTicket(edit = false) {
  const t = edit ? currentTicket : {};
  modal(
    edit ? '编辑工单' : '新建工单',
    `<div class="field"><label for="t-title">标题</label><input id="t-title" name="title" required maxlength="160" value="${esc(t.title || '')}" placeholder="简要描述遇到的问题"></div><div class="field"><label for="t-description">问题描述</label><textarea id="t-description" name="description" required maxlength="12000" rows="5" placeholder="发生了什么？何时开始？有哪些错误提示？">${esc(t.description || '')}</textarea></div><div class="form-grid"><div class="field"><label for="t-category">类别</label><select name="category" id="t-category">${options(config.categories, t.category, edit ? '' : '自动分类')}</select></div><div class="field"><label for="t-priority">优先级</label><select name="priority" id="t-priority">${options(config.priorities, t.priority || '普通')}</select></div></div><div class="field"><label for="t-requester">提交人（选填）</label><input name="requester" id="t-requester" maxlength="100" value="${esc(t.requester || '')}" placeholder="姓名或团队"></div><p class="settings-note">请不要在工单中填写密码、验证码或恢复码。</p>`,
    formFooter(edit ? '保存修改' : '创建工单'),
    edit ? 'edit-ticket-form' : 'new-ticket-form',
  );
}
function sourcesHTML(sources) {
  return sources
    .map(
      (s) =>
        `<a href="#document/${s.doc_id}?chunk=${s.chunk_id}" class="source-card"><h4>[${s.source_id}] ${esc(s.title)} ${s.current === false ? '· 已失效' : ''}</h4><p>${s.page ? '第 ' + s.page + ' 页 · ' : ''}片段 ${s.ordinal} · v${s.revision}</p><p>${esc(s.text.slice(0, 150))}${s.text.length > 150 ? '…' : ''}</p></a>`,
    )
    .join('');
}
function analysisHTML(t) {
  const loading = busyTickets.has(t.id);
  if (!t.analysis)
    return `<div class="ai-empty"><h3>先找到依据，再给出建议</h3><p>结合工单内容和本地知识库，辅助你判断下一步。</p><ul><li>建议类别、优先级和待补充信息</li><li>检索文档与已解决的相似工单</li><li>生成附带来源的回复草稿</li></ul>${btn(loading ? '<span class="spinner"></span>分析中…' : icon('spark') + ' 开始分析', 'analyse', 'primary', t.id)}<p class="tiny section-gap">首次模型加载可能需要一些时间。你可以离开页面，任务会在后台继续。</p></div>`;
  const a = t.analysis,
    r = a.result;
  const labels = {
    generated: '本地模型草稿',
    no_evidence: '缺少资料',
    extractive: '仅检索结果',
    insufficient: '证据不足',
  };
  return `<div class="panel-body">${a.stale ? '<div class="notice">工单或引用资料已变化。以下为历史分析，请重新分析后再采纳。</div>' : ''}${r.warning ? `<div class="notice">${esc(r.warning)}</div>` : ''}
 <div class="actions" style="margin-bottom:18px">${badge(labels[r.mode] || r.mode, r.mode === 'generated' ? 'green' : 'amber')}<span class="tiny muted">${r.retrieval_mode === 'hybrid' ? '语义 + 关键词检索' : '关键词检索'} · ${dateTime(a.created_at)}</span></div>
 <div class="ai-section"><h3>当前问题</h3><p>${esc(r.summary)}</p><div class="actions">${badge(r.category)}${priority(r.priority)}</div><p class="tiny muted" style="margin-top:9px">${esc(r.priority_reason)}</p>${btn('采用类别与优先级', 'apply-labels', 'small', t.id)}</div>
 ${r.missing_info?.length ? `<div class="ai-section"><h3>建议补充</h3><ul class="ai-steps">${r.missing_info.map((x) => `<li>${esc(x)}</li>`).join('')}</ul></div>` : ''}
 ${
   r.steps?.length
     ? `<div class="ai-section"><h3>排查建议</h3><ol class="ai-steps">${r.steps
         .map(
           (s) =>
             `<li>${esc(s.text)} ${s.source_ids
               .map((id) => {
                 const src = r.sources.find((x) => x.source_id === id);
                 return src
                   ? `<a class="source-link" href="#document/${src.doc_id}?chunk=${src.chunk_id}">[${id}]</a>`
                   : '';
               })
               .join('')}</li>`,
         )
         .join('')}</ol></div>`
     : ''
 }
 <div class="ai-section"><h3>回复草稿</h3><div class="prose">${esc(r.reply)}</div><div class="actions section-gap">${btn('使用此草稿', 'use-draft', 'soft small', t.id)}${btn('不采用', 'reject-draft', 'small', t.id)}</div>${a.feedback ? `<p class="tiny muted section-gap">反馈：${esc({ accepted: '已采纳', edited: '修改后采纳', rejected: '未采用' }[a.feedback])}</p>` : ''}</div>
 <div class="ai-section"><h3>参考资料 <span class="count-chip">${r.sources.length}</span></h3>${r.sources.length ? sourcesHTML(r.sources) : '<p class="muted">没有找到足够相关的资料。可先导入知识文档，再重新分析。</p>'}</div>
 ${r.similar_tickets?.length ? `<div class="ai-section"><h3>相似的已解决工单</h3>${r.similar_tickets.map((x) => `<p><a href="#ticket/${x.id}">#${x.id} ${esc(x.title)}</a></p>`).join('')}<p class="tiny muted">仅供参考，需确认适用条件。</p></div>` : ''}
 <p class="tiny muted section-gap">处理步骤使用已核对的原文摘录；是否适用仍需人工确认。${r.model ? '生成模型：' + esc(r.model) : ''}</p></div>`;
}
async function ticketView(id, epoch) {
  const t = await api('/tickets/' + id);
  if (epoch !== routeEpoch) return;
  currentTicket = t;
  $('#main').innerHTML =
    `<a class="back-link" href="#tickets">${icon('back')} 返回工单列表</a>` +
    head(
      t.title,
      `#${String(t.id).padStart(4, '0')} · ${esc(t.requester || '未填写提交人')} · 创建于 ${dateTime(t.created_at)} ${sample(t.sample)}`,
      btn('编辑', 'edit-ticket') + btn('删除', 'delete-ticket', 'ghost', t.id),
    ) +
    `<div class="detail-layout"><div><section class="panel"><div class="panel-head"><h2>问题详情</h2><div class="actions">${statusBadge(t.status)}${priority(t.priority)}</div></div><div class="panel-body"><div class="prose">${esc(t.description)}</div><div class="ticket-summary">${badge(t.category)}<span class="tiny muted">更新于 ${fullDate(t.updated_at)}</span></div></div></section>
 <section class="panel"><div class="panel-head"><h2>处理与回复</h2><span class="tiny muted">人工确认</span></div><form id="handling-form" class="panel-body"><div class="field"><label for="handling-status">工单状态</label><select id="handling-status" name="status">${options(config.statuses, t.status)}</select></div><div class="field"><label for="handling-reply">回复内容</label><textarea id="handling-reply" name="reply" rows="5" placeholder="编写回复，或使用右侧 AI 草稿">${esc(t.reply)}</textarea><div class="hint">仅保存在本地，不会自动发送给提交人。</div></div><div class="field"><label for="handling-resolution">解决方案</label><textarea id="handling-resolution" name="resolution" rows="4" placeholder="记录已经确认的原因与实际处理步骤">${esc(t.resolution)}</textarea><div class="hint">标记为已解决或已关闭前，需要填写解决方案。</div></div><div class="actions"><button class="btn primary" type="submit">保存处理结果</button>${['已解决', '已关闭'].includes(t.status) ? btn(icon('book') + ' 沉淀为知识', 'save-solution', '', t.id) : ''}</div></form></section>
 <section class="panel"><div class="panel-head"><h2>处理记录</h2><span class="tiny muted">${t.events.length} 条</span></div><div class="panel-body"><form id="note-form"><div class="field"><textarea name="body" required maxlength="8000" placeholder="添加排查进展或内部备注…" rows="2"></textarea></div><button class="btn small" type="submit">添加记录</button></form><div class="divider"></div>${t.events.map((e) => `<div class="event"><div class="event-header">${esc(e.kind)}<time>${dateTime(e.created_at)}</time></div><p>${esc(e.body)}</p></div>`).join('')}</div></section></div>
 <aside><section class="panel"><div class="panel-head ai-head"><h2 class="ai-title">${icon('spark')} AI 处理助手</h2><span id="analysis-header-actions">${t.analysis ? btn(busyTickets.has(t.id) ? '<span class="spinner"></span>分析中' : '重新分析', 'analyse', 'small', t.id) : ''}</span></div><div id="analysis-content">${analysisHTML(t)}</div></section></aside></div>`;
  if (busyTickets.has(t.id)) $$('[data-action="analyse"]').forEach((b) => (b.disabled = true));
  if (t.analysis?.stale)
    $$('[data-action="use-draft"],[data-action="apply-labels"]').forEach(
      (b) => (b.disabled = true),
    );
}
async function knowledgeView(epoch) {
  const docs = await api('/documents');
  if (epoch !== routeEpoch) return;
  const shown = docs.filter((d) =>
    docFilter === 'review' ? !d.approved : docFilter === 'ready' ? d.approved : true,
  );
  $('#main').innerHTML =
    head(
      '知识库',
      '把操作手册与解决经验，变成随时可查的知识。',
      btn(icon('plus') + ' 新建知识', 'write-document') +
        btn(icon('upload') + ' 导入文档', 'new-document', 'primary'),
    ) +
    `<div class="tabs" role="group" aria-label="知识库筛选">${[
      ['all', '全部文档', docs.length],
      ['ready', '已发布', docs.filter((d) => d.approved).length],
      ['review', '待审核', docs.filter((d) => !d.approved).length],
    ]
      .map(
        ([k, l, n]) =>
          `<button class="tab ${docFilter === k ? 'active' : ''}" data-action="filter-docs" data-id="${k}">${l} ${n}</button>`,
      )
      .join('')}</div>
 <section class="panel"><form id="knowledge-search" class="toolbar"><div class="search-field">${icon('search')}<input name="query" aria-label="检索知识库" required maxlength="4000" placeholder="试着问：VPN 认证超时，应该怎么排查？"></div><button type="submit" class="btn soft">检索知识</button></form><div id="search-results"></div></section>
 <div class="document-grid">${shown.map((d) => `<article class="doc-card"><div class="doc-top"><div class="file-icon">${d.filename.toLowerCase().endsWith('.pdf') ? 'PDF' : 'DOC'}</div>${docStatus(d)}</div><h3><a href="#document/${d.id}">${esc(d.title)}</a></h3><p>${d.chunks} 个片段 · v${d.revision} ${d.sample ? '· 示例' : ''}<br>${dateTime(d.updated_at)} 更新</p>${['queued', 'parsing', 'indexing'].includes(d.status) ? `<div class="progress" aria-label="索引进度 ${d.progress}%"><span style="width:${d.progress}%"></span></div>` : ''}${d.error ? `<p>${esc(d.error)}</p>` : ''}<div class="actions"><a class="btn small" href="#document/${d.id}">${d.approved ? '查看文档' : '审核内容'}</a>${d.approved ? btn(icon('refresh') + ' 重新索引', 'reindex', 'ghost small', d.id) : badge('审核后才参与检索', 'purple')}</div></article>`).join('')}</div>${!shown.length ? `<section class="panel">${empty(docFilter === 'review' ? '没有待审核的知识' : '知识库还是空的', docFilter === 'review' ? '从已解决工单保存的方案会出现在这里。' : '支持 Markdown、TXT 和文本型 PDF，单份不超过5MB。', docFilter === 'review' ? '' : btn('导入文档', 'new-document', 'primary'), 'book')}</section>` : ''}`;
  if (docs.some((d) => ['queued', 'parsing', 'indexing'].includes(d.status))) scheduleDocRefresh();
}
let docTimer;
function scheduleDocRefresh() {
  clearTimeout(docTimer);
  docTimer = setTimeout(() => {
    if (location.hash === '#knowledge' || location.hash.startsWith('#document/')) {
      if (
        $('#modal').open ||
        ['INPUT', 'TEXTAREA'].includes(document.activeElement?.tagName) ||
        $('#search-results')?.textContent.trim()
      )
        scheduleDocRefresh();
      else render();
    }
  }, 2500);
}
function newDocument(write = false, edit = false) {
  const d = edit ? currentDoc : {};
  modal(
    edit ? '编辑知识内容' : write ? '新建知识条目' : '导入文档',
    `${!write && !edit ? '<div class="file-drop"><label for="doc-file">选择文件</label><input id="doc-file" type="file" accept=".md,.txt,.pdf" required><p>支持 UTF-8 文本与文本型 PDF · 最多5MB / 200页</p></div>' : ''}<div class="field"><label for="doc-title">文档标题</label><input id="doc-title" name="title" required maxlength="160" value="${esc(d.title || '')}" placeholder="例如：VPN 连接排查指南"></div>${write || edit ? `<div class="field"><label for="doc-content">知识内容</label><textarea id="doc-content" name="content" required maxlength="500000" rows="11" placeholder="描述适用场景、已确认的步骤和注意事项…">${esc(d.content || '')}</textarea></div>` : ''}${edit ? '<div class="notice info">保存后生成新版本，旧索引立即停用。编辑 PDF 提取文字会将当前文档转为 Markdown；原 PDF 可在修改前下载保留。</div>' : '<p class="settings-note">导入后会在本机解析并建立索引。没有模型时仍可进行关键词检索。</p>'}`,
    formFooter(edit ? '保存新版本' : write ? '保存并建立索引' : '导入并建立索引'),
    edit ? 'edit-doc-form' : write ? 'write-doc-form' : 'upload-doc-form',
  );
  if (!write && !edit)
    $('#doc-file').onchange = () => {
      const f = $('#doc-file').files[0];
      if (f && !$('#doc-title').value) $('#doc-title').value = f.name.replace(/\.[^.]+$/, '');
    };
}
async function documentView(id, epoch) {
  const d = await api('/documents/' + id);
  if (epoch !== routeEpoch) return;
  currentDoc = d;
  const chunk = new URLSearchParams(location.hash.split('?')[1] || '').get('chunk');
  $('#main').innerHTML =
    `<a class="back-link" href="#knowledge">${icon('back')} 返回知识库</a>` +
    head(
      d.title,
      `${esc(d.filename)} · v${d.revision} · ${dateTime(d.updated_at)} 更新 ${sample(d.sample)}`,
      btn('编辑内容', 'edit-document') + btn('删除', 'delete-document', 'ghost', d.id),
    ) +
    `${!d.approved ? `<div class="onboard"><div><h3>等待人工审核</h3><p>核对适用条件、处理步骤和敏感信息后再发布。${d.source_ticket ? ` <a href="#ticket/${d.source_ticket}">查看来源工单 #${d.source_ticket}</a>` : ''}</p></div>${btn(icon('check') + ' 审核并发布', 'approve-document', 'primary', d.id)}</div>` : ''}
 ${d.error ? `<div class="notice ${d.status === 'failed' ? 'error' : ''}">${esc(d.error)}</div>` : ''}
 <section class="panel"><div class="panel-head"><div class="actions">${docStatus(d)}<span class="tiny muted">${d.chunks.length} 个片段 ${d.embed_model ? '· ' + esc(d.embed_model) : ''}</span></div><div class="actions"><a class="btn small" href="/api/documents/${d.id}/file">下载原文件</a>${d.approved ? btn('重新索引', 'reindex', 'small', d.id) : ''}</div></div><div class="panel-body">${['queued', 'parsing', 'indexing'].includes(d.status) ? `<p class="settings-note"><span class="spinner"></span>正在处理，进度 ${d.progress}%</p><div class="progress"><span style="width:${d.progress}%"></span></div>` : ''}<div class="document-content">${esc(d.content || '文件尚未完成解析。')}</div></div></section>
 ${d.chunks.length ? `<section class="panel"><div class="panel-head"><h2>可引用的原文片段</h2><span class="tiny muted">保留原文，不由模型改写</span></div><div class="panel-body">${d.chunks.map((c) => `<div class="chunk" id="chunk-${c.id}" ${String(c.id) === chunk ? 'style="border:2px solid var(--accent)"' : ''}>${badge('片段 ' + c.ordinal + (c.page ? ' · 第 ' + c.page + ' 页' : ''))}<div class="prose">${esc(c.text)}</div></div>`).join('')}</div></section>` : ''}`;
  if (chunk) document.getElementById('chunk-' + chunk)?.scrollIntoView({ block: 'center' });
  if (['queued', 'parsing', 'indexing'].includes(d.status)) scheduleDocRefresh();
}
async function settingsView(epoch) {
  const [bootstrap, models] = await Promise.all([api('/bootstrap'), api('/models')]);
  if (epoch !== routeEpoch) return;
  config = bootstrap;
  modelState = models;
  updateModelPill(models);
  const s = config.settings;
  $('#main').innerHTML =
    head('设置', '管理本地模型、数据与备份。') +
    `<div class="settings-grid"><section class="panel"><div class="panel-head"><h2>本地模型</h2>${badge(models.online ? '服务在线' : '服务离线', models.online ? 'green' : 'amber')}</div><form id="settings-form" class="panel-body"><div class="notice info">仅连接本机 Ollama，不使用云端推理。首次使用需先下载模型。</div><div class="field"><label for="s-endpoint">服务地址</label><input id="s-endpoint" name="endpoint" value="${esc(s.endpoint)}" required></div><div class="field"><label for="s-chat">回复生成模型</label><input id="s-chat" name="chat_model" list="installed-models" value="${esc(s.chat_model)}" required></div><div class="field"><label for="s-embed">语义检索模型</label><input id="s-embed" name="embed_model" list="installed-models" value="${esc(s.embed_model)}" required><div class="hint">更换检索模型或服务地址后，请为知识文档重新建立索引。</div></div><datalist id="installed-models">${models.models.map((m) => `<option value="${esc(m)}">`).join('')}</datalist><div class="actions"><button type="submit" class="btn primary">保存配置</button>${btn('测试已保存配置', 'test-models')}</div><div id="model-test" class="section-gap"></div><p class="settings-note section-gap">已安装 ${models.models.length} 个模型</p><div class="model-list">${models.models.map((m) => badge(m)).join('')}</div></form></section>
 <div><section class="panel"><div class="panel-head"><h2>数据与备份</h2>${icon('shield')}</div><div class="panel-body"><p class="settings-note">工单、处理记录、文档原件和向量索引一并保存在本机。备份包含这些内容，可用于完整恢复。</p><div class="field"><label>数据位置</label><div class="data-path">${esc(config.data_dir)}</div></div>${btn(icon('download') + ' 创建并下载备份', 'backup', 'primary')}<div id="backup-result"></div><details><summary>如何恢复备份</summary><ol class="restore-steps"><li>先停止正在运行的工作台，避免覆盖使用中的数据。</li><li>在项目目录运行下方命令，将路径替换为备份 ZIP 的实际位置。</li><li>恢复完成后重新启动。恢复前会自动保留现有数据库副本。</li></ol><pre>.venv/bin/python app.py --restore /备份文件路径.zip\n.venv/bin/python app.py</pre></details></div></section>
 <section class="panel"><div class="panel-head"><h2>体验与边界</h2></div><div class="panel-body"><p class="settings-note">第一版面向本机单用户，不提供多人权限或对外发送功能。示例资料仅用于体验，应替换为你自己的正式知识。</p>${btn('载入示例工单和知识', 'seed')}<div class="divider"></div><a class="subtle-link" href="/classic" target="_blank" rel="noopener">查看原版分类与评估 Demo ↗</a></div></section></div></div>`;
}
function updateModelPill(s) {
  const online = s.online && s.chat_ready && s.embed_ready;
  $('#model-pill').className = 'model-pill' + (online ? '' : ' off');
  $('#model-pill').innerHTML =
    '<i></i>' + (online ? '本地模型就绪' : s.online ? '模型待配置' : '模型离线 · 可人工处理');
}
async function render() {
  const epoch = ++routeEpoch;
  clearTimeout(docTimer);
  const hash = location.hash.slice(1) || 'dashboard',
    route = hash.split('?')[0],
    base = route.split('/')[0];
  const selected = base === 'ticket' ? 'tickets' : base === 'document' ? 'knowledge' : base;
  $$('#nav a').forEach((a) => {
    a.classList.toggle('active', a.dataset.route === selected);
    if (a.dataset.route === selected) a.setAttribute('aria-current', 'page');
    else a.removeAttribute('aria-current');
  });
  $('#breadcrumb').textContent =
    '工作空间 / ' +
    ({ dashboard: '工作台', tickets: '工单', knowledge: '知识库', settings: '设置' }[selected] ||
      '工作台');
  try {
    if (base === 'dashboard') await dashboard(epoch);
    else if (base === 'tickets') await ticketsView(epoch);
    else if (base === 'ticket' && /^\d+$/.test(route.split('/')[1]))
      await ticketView(route.split('/')[1], epoch);
    else if (base === 'knowledge') await knowledgeView(epoch);
    else if (base === 'document' && /^\d+$/.test(route.split('/')[1]))
      await documentView(route.split('/')[1], epoch);
    else if (base === 'settings') await settingsView(epoch);
    else location.hash = '#dashboard';
  } catch (e) {
    if (epoch === routeEpoch)
      $('#main').innerHTML =
        `<section class="panel">${empty('暂时无法打开', esc(e.message), btn('重新加载', 'reload'))}</section>`;
  }
}
async function analyseTicket(id) {
  if (busyTickets.has(id)) return;
  busyTickets.add(id);
  $$('[data-action="analyse"]').forEach((b) => {
    b.disabled = true;
    b.innerHTML = '<span class="spinner"></span>分析中…';
  });
  try {
    const { job_id } = await api(`/tickets/${id}/analyse`, {});
    await jobWait(job_id);
    toast('分析完成，请核对资料和回复草稿');
    if (location.hash.split('?')[0] === '#ticket/' + id) {
      const t = await api('/tickets/' + id);
      currentTicket.analysis = t.analysis;
      $('#analysis-content').innerHTML = analysisHTML(t);
      $('#analysis-header-actions').innerHTML = btn('重新分析', 'analyse', 'small', t.id);
    }
  } finally {
    busyTickets.delete(id);
    $$('[data-action="analyse"]').forEach((b) => {
      b.disabled = false;
      b.textContent = '重新分析';
    });
  }
}
async function act(action, id) {
  switch (action) {
    case 'close-modal':
      $('#modal').close();
      break;
    case 'reload':
      await render();
      break;
    case 'new-ticket':
      newTicket();
      break;
    case 'edit-ticket':
      newTicket(true);
      break;
    case 'new-document':
      newDocument();
      break;
    case 'write-document':
      newDocument(true);
      break;
    case 'edit-document':
      newDocument(true, true);
      break;
    case 'filter-docs':
      docFilter = id;
      await render();
      break;
    case 'seed': {
      const result = await api('/seed', {});
      toast(result.message);
      await render();
      break;
    }
    case 'delete-ticket':
      confirmAction(
        '删除工单？',
        '工单和处理记录会被删除，已发布的独立知识文档会保留。',
        async () => {
          await api('/tickets/' + id, {}, 'DELETE');
          location.hash = '#tickets';
          toast('工单已删除');
        },
        '删除工单',
      );
      break;
    case 'delete-document':
      confirmAction(
        '删除知识文档？',
        '文档与索引会一起删除，后续检索不再引用。历史分析中的引用会标记为失效。',
        async () => {
          await api('/documents/' + id, {}, 'DELETE');
          location.hash = '#knowledge';
          toast('文档已删除');
        },
        '删除文档',
      );
      break;
    case 'analyse':
      await analyseTicket(Number(id));
      break;
    case 'apply-labels': {
      const a = currentTicket.analysis;
      if (!a || a.stale) throw new Error('请重新分析后再采用建议');
      await api(
        '/tickets/' + id,
        {
          version: currentTicket.version,
          category: a.result.category,
          priority: a.result.priority,
        },
        'PATCH',
      );
      toast('类别与优先级已更新，请重新分析以匹配最新工单');
      await render();
      break;
    }
    case 'use-draft': {
      const a = currentTicket.analysis;
      if (!a || a.stale) throw new Error('资料或工单已变化，请重新分析');
      const value = $('#handling-reply').value;
      const apply = () => {
        $('#handling-reply').value = a.result.reply;
        $('#handling-reply').dataset.analysisId = a.id;
        $('#handling-reply').focus();
        toast('草稿已填入，请核对后保存处理结果');
      };
      if (value && value !== a.result.reply)
        confirmAction(
          '替换当前回复？',
          '当前回复内容将被 AI 草稿替换，尚未保存的文字会丢失。',
          async () => apply(),
          '替换',
        );
      else apply();
      break;
    }
    case 'reject-draft':
      await api('/tickets/' + id + '/feedback', {
        analysis_id: currentTicket.analysis.id,
        feedback: 'rejected',
      });
      toast('已记录：不采用建议');
      break;
    case 'save-solution': {
      const result = await api('/tickets/' + id + '/solution', {});
      toast('解决方案已进入待审核队列');
      location.hash = '#document/' + result.id;
      break;
    }
    case 'reindex':
    case 'approve-document': {
      const result = await api(
        `/documents/${id}/${action === 'reindex' ? 'reindex' : 'approve'}`,
        {},
      );
      toast('索引任务已开始');
      await render();
      jobWait(result.job_id)
        .then(() => {
          toast('文档处理完成');
          if (location.hash.startsWith('#knowledge') || location.hash.startsWith('#document/'))
            render();
        })
        .catch((e) => {
          toast(e.message, true);
          render();
        });
      break;
    }
    case 'test-models': {
      $('#model-test').innerHTML =
        '<p class="settings-note"><span class="spinner"></span>正在测试向量和生成模型…</p>';
      const r = await api('/models/test', {});
      const result = await jobWait(r.job_id);
      if ($('#model-test'))
        $('#model-test').innerHTML =
          `<div class="notice success">连接测试通过：${result.embedding_dimensions} 维向量，文本生成正常。</div>`;
      toast('本地模型连接测试通过');
      break;
    }
    case 'backup': {
      const r = await api('/backup', {});
      if ($('#backup-result'))
        $('#backup-result').innerHTML =
          `<p class="settings-note section-gap">备份已创建：<a href="${esc(r.url)}" download>${esc(r.filename)}</a></p>`;
      const a = document.createElement('a');
      a.href = r.url;
      a.download = r.filename;
      a.click();
      toast('备份已创建');
      break;
    }
  }
}
document.addEventListener('click', async (e) => {
  const b = e.target.closest('[data-action]');
  if (!b || b.disabled) return;
  e.preventDefault();
  const action = b.dataset.action;
  const old = b.disabled;
  b.disabled = true;
  try {
    await act(action, b.dataset.id);
  } catch (err) {
    toast(err.message, true);
    if (action === 'test-models' && $('#model-test'))
      $('#model-test').innerHTML = `<div class="notice error">${esc(err.message)}</div>`;
  } finally {
    b.disabled = old;
  }
});
function toBase64(file) {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result).split(',')[1]);
    r.onerror = () => reject(new Error('文件读取失败'));
    r.readAsDataURL(file);
  });
}
document.addEventListener('submit', async (e) => {
  const f = e.target;
  if (!(f instanceof HTMLFormElement)) return;
  e.preventDefault();
  const data = Object.fromEntries(new FormData(f)),
    b = f.querySelector('[type="submit"]');
  if (b?.disabled) return;
  if (b) b.disabled = true;
  try {
    switch (f.id) {
      case 'ticket-filter':
        location.hash = '#tickets?' + new URLSearchParams(data);
        await render();
        break;
      case 'new-ticket-form': {
        const t = await api('/tickets', data);
        $('#modal').close();
        location.hash = '#ticket/' + t.id;
        toast('工单已创建');
        break;
      }
      case 'edit-ticket-form':
        await api(
          '/tickets/' + currentTicket.id,
          { ...data, version: currentTicket.version },
          'PATCH',
        );
        $('#modal').close();
        await render();
        toast('工单已更新');
        break;
      case 'handling-form': {
        const aid = $('#handling-reply').dataset.analysisId;
        if (aid) data.analysis_id = Number(aid);
        await api(
          '/tickets/' + currentTicket.id,
          { ...data, version: currentTicket.version },
          'PATCH',
        );
        await render();
        toast('处理结果已保存');
        break;
      }
      case 'note-form':
        await api('/tickets/' + currentTicket.id + '/notes', data);
        await render();
        toast('处理记录已添加');
        break;
      case 'upload-doc-form':
      case 'write-doc-form':
      case 'edit-doc-form': {
        if (f.id === 'upload-doc-form') {
          const file = $('#doc-file').files[0];
          if (!file) throw new Error('请选择文件');
          if (file.size > 5 * 1024 * 1024) throw new Error('文件不能超过5MB');
          data.filename = file.name;
          data.file_base64 = await toBase64(file);
        }
        const edit = f.id === 'edit-doc-form';
        if (edit) data.revision = currentDoc.revision;
        const r = await api(
          '/documents' + (edit ? '/' + currentDoc.id : ''),
          data,
          edit ? 'PATCH' : 'POST',
        );
        $('#modal').close();
        location.hash = '#document/' + r.id;
        await render();
        toast(r.job_id ? '文档已保存，正在建立索引' : '知识草稿已保存，等待审核');
        break;
      }
      case 'knowledge-search': {
        $('#search-results').innerHTML =
          '<div class="panel-body"><span class="spinner"></span>正在检索本地知识…</div>';
        const job = await api('/search', data);
        const r = await jobWait(job.job_id);
        if (!$('#search-results')) break;
        $('#search-results').innerHTML =
          `<div class="panel-body"><div class="actions">${badge(r.mode === 'hybrid' ? '语义 + 关键词' : '关键词检索', 'green')}${btn('清空结果', 'clear-search', 'ghost small')}</div>${r.warning ? `<p class="settings-note section-gap">${esc(r.warning)}</p>` : ''}<div class="search-results">${r.sources.length ? r.sources.map((s) => `<article class="result-card"><h3><a href="#document/${s.doc_id}?chunk=${s.chunk_id}">${esc(s.title)}</a></h3><p>${esc(s.text)}</p><span class="tiny muted">${s.page ? '第 ' + s.page + ' 页 · ' : ''}片段 ${s.ordinal} · 相关度指标 ${s.score.toFixed(2)}（非正确率）</span></article>`).join('') : empty('没有找到足够相关的资料', '尝试具体的错误提示，或补充对应操作文档。', '', 'search')}</div></div>`;
        break;
      }
      case 'settings-form': {
        const result = await api('/settings', data);
        config.settings = result.settings;
        toast(result.reindex_required ? '配置已保存，请重新索引知识文档' : '配置已保存');
        await render();
        break;
      }
    }
  } catch (err) {
    if ($('#modal').open && f.closest('dialog')) {
      $('#modal-error').className = 'modal-error';
      $('#modal-error').textContent = err.message;
    } else {
      toast(err.message, true);
      if (f.id === 'knowledge-search' && $('#search-results'))
        $('#search-results').innerHTML =
          `<div class="panel-body"><div class="notice error">${esc(err.message)}</div></div>`;
    }
  } finally {
    if (b) b.disabled = false;
  }
});
document.addEventListener('click', (e) => {
  if (e.target.closest('[data-action="clear-search"]')) $('#search-results').innerHTML = '';
});
window.addEventListener('hashchange', () => {
  window.scrollTo(0, 0);
  render();
});
async function boot() {
  $('#date').textContent = new Date().toLocaleDateString('zh-CN', {
    month: 'long',
    day: 'numeric',
    weekday: 'long',
  });
  $('#nav').innerHTML = [
    ['dashboard', '工作台', 'dashboard'],
    ['tickets', '工单', 'tickets'],
    ['knowledge', '知识库', 'book'],
    ['settings', '设置', 'settings'],
  ]
    .map(
      ([route, label, i]) =>
        `<a href="#${route}" data-route="${route}" class="nav-link">${icon(i)}${label}</a>`,
    )
    .join('');
  try {
    config = await api('/bootstrap');
    const queued = await api('/jobs');
    queued
      .filter((j) => j.kind === 'analysis')
      .forEach((j) => {
        busyTickets.add(j.target_id);
        jobWait(j.id)
          .then(async () => {
            busyTickets.delete(j.target_id);
            if (location.hash.split('?')[0] === '#ticket/' + j.target_id) {
              const t = await api('/tickets/' + j.target_id);
              currentTicket.analysis = t.analysis;
              $('#analysis-content').innerHTML = analysisHTML(t);
              $('#analysis-header-actions').innerHTML = btn('重新分析', 'analyse', 'small', t.id);
              $$('[data-action="analyse"]').forEach((b) => {
                b.disabled = false;
                b.textContent = '重新分析';
              });
            }
            toast('后台分析完成');
          })
          .catch((e) => {
            busyTickets.delete(j.target_id);
            toast(e.message, true);
          });
      });
    await render();
    api('/models')
      .then((s) => {
        modelState = s;
        updateModelPill(s);
      })
      .catch(() => updateModelPill({ online: false }));
  } catch (e) {
    $('#main').innerHTML = empty(
      '无法连接工作台',
      esc(e.message),
      '<button class="btn" id="retry-boot">重试</button>',
    );
    $('#retry-boot').onclick = boot;
  }
}
boot();
