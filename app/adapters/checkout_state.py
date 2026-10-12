"""Read checkout controls together and wake on DOM changes, without order APIs.

The body text stays in memory for the existing challenge/consent guard. Never
persist a snapshot: it can include contact details on a later checkout page.
"""

CHECKOUT_STATE_SCRIPT = r'''({wait, wanted}) => {
  const visible = e => !!e.getClientRects().length &&
    !['hidden','collapse'].includes(getComputedStyle(e).visibility);
  const text = e => (e?.innerText || e?.textContent || '').replace(/\s+/g,' ').trim();
  const name = e => e.getAttribute('aria-label') ||
    Array.from(e.labels || []).map(text).join(' ').trim() ||
    (e.getAttribute('aria-labelledby') || '').split(/\s+/).map(id => text(document.getElementById(id))).join(' ').trim() ||
    text(e);
  const read = () => {
    const root = document.querySelector('main,[role="main"]') || document.body || document.documentElement;
    const buttons = Array.from(document.querySelectorAll('button,[role="button"],input[type="submit"],input[type="button"]'))
      .filter(visible).map(e => name(e) || e.value || '');
    const radios = Array.from(root?.querySelectorAll('input[type="radio"],[role="radio"]') || []).filter(visible)
      .map(e => ({label:name(e),id:e.id,value:e.value || '',checked:e.checked || e.getAttribute('aria-checked')==='true',
                 disabled:e.disabled || e.getAttribute('aria-disabled')==='true'}));
    const headings = Array.from(root?.querySelectorAll('h1,h2,h3,[role="heading"]') || []).filter(visible).map(text);
    const fields = Array.from(root?.querySelectorAll('input:not([type="radio"]):not([type="checkbox"]):not([type="password"]),[role="textbox"]') || [])
      .filter(visible).map(name);
    const knownFields = ['姓氏','名字','电子邮件地址','联系人手机号码','政府颁发的身份证件号码的后四位'];
    const contactFields = knownFields.filter(label => fields.some(n => n===label || n.startsWith(label+' ')));
    const contact = contactFields.includes('姓氏') && contactFields.includes('名字') &&
      (contactFields.includes('电子邮件地址') || contactFields.includes('联系人手机号码'));
    let stage = 'loading';
    if (buttons.includes('同意并继续')) stage = 'consent';
    else if (headings.some(h => /你希望如何付款/.test(h))) stage = 'payment';
    else if (contact || buttons.some(b => /^继续选择付款方式(?:\s|$)/.test(b))) stage = 'contact';
    else if (buttons.includes('以游客身份继续')) stage = 'guest';
    else if (buttons.includes('继续填写取货详情') || buttons.includes('选择取货零售店') ||
             radios.some(r => /^(我要)?取货(?:\s|$)/.test(r.label))) stage = 'pickup';
    const diagnosticButtons = Array.from(root?.querySelectorAll('button,[role="button"],input[type="submit"],input[type="button"]') || [])
      .filter(visible).map(e => name(e) || e.value || '');
    const hints = ['继续','继续选择付款方式','继续填写取货详情','验证','获取验证码','重试'];
    const continuationButtons = hints.filter(label => buttons.some(b => b===label || b.startsWith(label+' ')));
    return {stage,buttons,radios,diagnostic_buttons:diagnosticButtons,contact_fields:contactFields,
            continuation_buttons:continuationButtons,text:document.body?.innerText || ''};
  };
  const initial = read();
  const ready = s => !wanted.length || wanted.includes(s.stage);
  if (!wait || ready(initial)) return initial;
  // A single browser observation replaces repeated button-by-button RPCs.
  // Short slices keep Stop responsive while DOM mutations wake immediately.
  return new Promise(resolve => {
    const finish = s => { observer.disconnect(); clearTimeout(timer); resolve(s); };
    const observer = new MutationObserver(() => { const s=read(); if (ready(s)) finish(s); });
    observer.observe(document.documentElement,{subtree:true,childList:true,attributes:true,characterData:true});
    const timer=setTimeout(() => finish(read()),wait);
  });
}'''


async def checkout_state(page, *, wait=0, wanted=()):
    return await page.evaluate(CHECKOUT_STATE_SCRIPT, {'wait': wait, 'wanted': list(wanted)})
