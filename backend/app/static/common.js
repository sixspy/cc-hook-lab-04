const 投递台 = {
  async getJSON(url, opt) {
    const r = await fetch(url, opt);
    if (r.status === 401) { location.href = '/login'; throw new Error('未登录'); }
    const data = await r.json().catch(() => ({}));
    if (!r.ok) {
      const error = new Error(data.错误 || data.error || r.statusText);
      error.data = data;
      error.status = r.status;
      throw error;
    }
    return data;
  },
  async postJSON(url, body) {
    return this.getJSON(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    });
  },
  安全文本(s) {
    const d = document.createElement('div');
    d.textContent = s == null ? '' : String(s);
    return d.innerHTML;
  },
  徽章(状态) {
    const span = document.createElement('span');
    const key = String(状态 || '—');
    span.className = '徽章 ' + key;
    span.dataset.status = key;
    span.textContent = key;
    return span;
  },
  行(html) {
    const tr = document.createElement('tr');
    tr.innerHTML = html;
    return tr;
  },
  空态(msg) { return msg || '暂无数据'; },
  查询参数(name) {
    return new URLSearchParams(location.search).get(name) || '';
  },
  事件链接(eid, text) {
    return `<a class="事件链" href="/事件详情.html?事件号=${encodeURIComponent(eid || '')}">${this.安全文本(text || eid || '')}</a>`;
  },
  命运文案(状态, row) {
    if (row && row.命运) return row.命运;
    if (row && row.拒因) return `已拒绝：${row.拒因}`;
    const map = {
      '待投': '待投：已入队，等待签名规现行算法签发',
      '投递中': '投递中：已有失败尝试，可按签名规继续重试',
      '已投递': '已投递：应有现行算法版本与指纹的成功尝试',
      '死信': '死信：重试耗尽，已入死信匣，可重投或作废',
      '待处置': '待处置：死信卡片等待重投或作废',
      '已重投': '已重投：事件回到待投，下一次投递认现行算法',
      '已作废': '已作废：死信结案，不再进入投递',
      '成功': '尝试成功：签名版本、指纹、摘要与库内账册同源',
      '失败': '尝试失败：可在本行读到响应码与中文原因',
      '拒绝': '已拒绝：旧算法或旧算回刷不得进入现行已投递集合',
    };
    let text = map[状态] || (状态 ? `命运：${状态}` : '暂无命运');
    if (row && row.说明 && 状态 === '失败' && !row.拒因) text += `；${row.说明}`;
    return text;
  },
  命运节点(状态, row) {
    const wrap = document.createElement('div');
    wrap.className = '命运单元';
    wrap.appendChild(this.徽章(状态));
    const small = document.createElement('small');
    small.className = '命运文';
    small.textContent = this.命运文案(状态, row);
    wrap.appendChild(small);
    return wrap;
  },
};
