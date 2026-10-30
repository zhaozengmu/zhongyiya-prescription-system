/* TCM-RMS 前端交互脚本
   只做四件事：toast 自动消失、侧栏折叠、按钮防重复提交、统一 API 请求。 */
(function () {
  'use strict';

  function getCookie(name) {
    const hit = document.cookie.split('; ').find((row) => row.startsWith(name + '='));
    return hit ? decodeURIComponent(hit.split('=')[1]) : '';
  }

  /* CSRF 令牌：优先读 <meta>，因为 CSRF_COOKIE_HTTPONLY=True 时 JS 读不到 cookie。
     这一点很关键——之前页面既没有 csrf_token 表单字段、JS 又读不到 cookie，
     所有 POST 接口都会 403。 */
  function getCsrf() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    if (meta && meta.content && meta.content !== 'NOTPROVIDED') return meta.content;
    const input = document.querySelector('[name=csrfmiddlewaretoken]');
    if (input && input.value) return input.value;
    return getCookie('csrftoken');
  }
  window.tcmCsrf = getCsrf;

  /* ---------- toast ---------- */
  function dismiss(el) {
    if (!el || el.dataset.closing) return;
    el.dataset.closing = '1';
    el.style.transition = 'opacity .18s, transform .18s';
    el.style.opacity = '0';
    el.style.transform = 'translateX(12px)';
    setTimeout(() => el.remove(), 190);
  }

  const wrap = document.getElementById('toastWrap');
  if (wrap) {
    wrap.querySelectorAll('.toast-item').forEach((item) => {
      const close = item.querySelector('.x');
      if (close) close.addEventListener('click', () => dismiss(item));
      setTimeout(() => dismiss(item), 4200);
    });
  }

  window.tcmToast = function (text, kind) {
    let host = document.getElementById('toastWrap');
    if (!host) {
      host = document.createElement('div');
      host.id = 'toastWrap';
      host.className = 'toast-wrap';
      document.body.appendChild(host);
    }
    const el = document.createElement('div');
    el.className = 'toast-item ' + (kind || 'info');
    el.innerHTML = '<i class="bi bi-info-circle"></i><div></div><button type="button" class="x">&times;</button>';
    el.querySelector('div').textContent = text;
    el.querySelector('.x').addEventListener('click', () => dismiss(el));
    host.appendChild(el);
    setTimeout(() => dismiss(el), 4200);
  };

  /* ---------- 侧栏折叠（移动端） ---------- */
  const toggle = document.getElementById('navToggle');
  const sidebar = document.getElementById('sidebar');
  if (toggle && sidebar) {
    toggle.addEventListener('click', () => sidebar.classList.toggle('open'));
    document.addEventListener('click', (e) => {
      if (window.innerWidth > 991) return;
      if (!sidebar.contains(e.target) && !toggle.contains(e.target)) sidebar.classList.remove('open');
    });
  }

  /* ---------- 按钮防重复提交 ---------- */
  document.addEventListener('submit', (e) => {
    const form = e.target;
    if (form.dataset.noLock === '1') return;
    const btn = form.querySelector('button[type="submit"]');
    if (!btn || btn.dataset.loading === '1') return;
    btn.dataset.loading = '1';
    const original = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>处理中…';
    setTimeout(() => {
      // 页面没有跳转说明提交失败，恢复按钮避免用户卡死
      btn.disabled = false;
      btn.innerHTML = original;
      btn.dataset.loading = '0';
    }, 8000);
  });

  /* ---------- 统一 API 请求 ---------- */
  window.tcmApi = async function (url, options) {
    const opts = Object.assign({ method: 'GET', headers: {} }, options || {});
    opts.headers = Object.assign(
      { 'X-CSRFToken': getCsrf(), 'Content-Type': 'application/json' },
      opts.headers
    );
    if (opts.body && typeof opts.body !== 'string') opts.body = JSON.stringify(opts.body);

    let resp;
    try {
      resp = await fetch(url, opts);
    } catch (err) {
      throw new Error('网络异常，请检查连接后重试');
    }

    let payload = null;
    try {
      payload = await resp.json();
    } catch (err) {
      throw new Error('服务返回格式异常（HTTP ' + resp.status + '）');
    }
    if (!resp.ok || (payload && payload.code && payload.code !== 200)) {
      throw new Error((payload && payload.message) || '操作失败（HTTP ' + resp.status + '）');
    }
    return payload;
  };
})();
