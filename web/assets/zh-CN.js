// Streamlit 暂无全局中文配置：补齐框架自带的可见文字及无障碍标签。
// 只匹配已知控件文案，不修改输入值、业务数据、接口请求或通知原文。
(() => {
  const labels = {
    'Skip to main content':'跳转到主要内容', 'Sidebar':'侧栏',
    'Collapse sidebar':'收起侧栏','Expand sidebar':'展开侧栏',
    'Link to heading':'跳转到标题','Main menu':'主菜单','Deploy':'发布',
    'Open':'展开','Close':'关闭','Clear':'清除','Search':'搜索','Dismiss':'关闭菜单',
    'Choose date':'选择日期','Previous month':'上个月','Next month':'下个月',
    'Previous year':'上一年','Next year':'下一年','Today':'今天',
    'Start date':'开始日期','End date':'结束日期',
    'Use arrow keys to navigate dates. Enter to select. Escape to close.':'使用方向键选择日期，按回车确认，按退出键关闭。',
    'year':'年','month':'月','day':'日','hour':'小时','minute':'分钟',
    'Increment':'增加','Decrement':'减少','Increase':'增加','Decrease':'减少',
    'Show password text':'显示密码','Hide password text':'隐藏密码',
    'Send message':'发送消息','Send':'发送','Your message':'你的消息',
    'Select an option':'请选择','Choose an option':'请选择','Select a value':'请选择',
    'Type to search':'输入内容搜索','No results found':'未找到结果',
    'No options':'暂无选项','No options found':'暂无选项','Clear all':'全部清除',
    'Press Enter to apply':'按回车应用','Press Enter to submit':'按回车提交',
    'Press Enter to submit form':'按回车提交表单',
    'Press ⌘+Enter to submit form':'按组合键提交表单',
    'Press Ctrl+Enter to submit form':'按组合键提交表单',
    'Press ⌘+Enter to apply':'按组合键应用','Press Ctrl+Enter to apply':'按组合键应用',
    'Download':'下载','Copy':'复制','Copied!':'已复制','Fullscreen':'全屏',
    'View fullscreen':'全屏查看','Close fullscreen':'退出全屏','Submit':'提交',
    'Running...':'正在处理…','Please wait...':'请稍候…',
    'This app has encountered an error.':'页面遇到错误，请刷新后重试。',
  };
  const translate = value => {
    if (!value) return value;
    const trimmed=value.trim();
    if (labels[trimmed]) return value.replace(trimmed,labels[trimmed]);
    for (const prefix of ['Choose date','year','month','day']) {
      if (trimmed.startsWith(prefix+', ')) return value.replace(prefix,labels[prefix]);
    }
    if (/^\d+ options available\./.test(trimmed)) return value.replace(/(\d+) options available\./, '有 $1 个可选项。').replace(/, selected\b/g,'，已选择');
    if (/, selected$/.test(trimmed)) return value.replace(/, selected$/, '，已选择');
    return value;
  };
  const apply=()=>{
    document.documentElement.lang='zh-CN';
    const root=document.body;
    if(!root) return;
    const walker=document.createTreeWalker(root,NodeFilter.SHOW_TEXT);
    let node;
    while((node=walker.nextNode())){
      if(node.parentElement?.closest('script,style,textarea,[contenteditable="true"],pre,code')) continue;
      const next=translate(node.nodeValue);
      if(next!==node.nodeValue)node.nodeValue=next;
    }
    root.querySelectorAll('[aria-label],[title],[placeholder],[aria-roledescription]').forEach(el=>{
      for(const attr of ['aria-label','title','placeholder','aria-roledescription']){
        const old=el.getAttribute(attr),next=translate(old);
        if(next!==old)el.setAttribute(attr,next);
      }
    });
  };
  if(window.__campusChineseUi){window.__campusChineseUi.apply();return;}
  let pending=false;
  const observer=new MutationObserver(()=>{
    if(pending)return;pending=true;
    requestAnimationFrame(()=>{pending=false;apply();});
  });
  window.__campusChineseUi={apply,observer};
  apply();
  observer.observe(document.body,{childList:true,subtree:true,characterData:true,attributes:true,attributeFilter:['aria-label','title','placeholder','aria-roledescription']});
})();
