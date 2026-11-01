(function () {
  'use strict';
  const button = document.getElementById('app-install');
  const status = document.getElementById('app-install-status');
  if (!button || !status) return;
  const ko = document.documentElement.lang === 'ko';
  const copy = ko ? {
    ready: '이 브라우저에서 설치할 수 있어요. 앱 설치를 누르면 브라우저가 확인을 요청합니다.',
    installed: '앱으로 열려 있어요. 아래에서 작업을 바로 이어가세요.',
    done: '설치가 완료됐어요. 홈 화면이나 앱 목록에서 Trade Paper AI를 열어보세요.',
    accepted: '설치를 요청했어요. 완료되면 홈 화면이나 앱 목록에서 아이콘을 확인하세요.',
    dismissed: '설치하지 않았어요. 웹사이트에서 계속 사용하거나 브라우저 메뉴에서 나중에 추가할 수 있어요.',
    failed: '설치 창을 열지 못했어요. 아래 기기별 안내에 따라 브라우저 메뉴를 이용하세요.'
  } : {
    ready: 'Installation is available in this browser. Install app opens the browser confirmation.',
    installed: 'You are using the app window. Continue with a task below.',
    done: 'App installed. Open Trade Paper AI from your home screen or app list.',
    accepted: 'Installation requested. Look for the icon on your home screen or app list when it completes.',
    dismissed: 'Not installed. Keep using the website, or add it later from your browser menu.',
    failed: 'Could not open installation. Follow the device instructions below using your browser menu.'
  };
  const display = window.matchMedia('(display-mode: standalone)');
  const inApp = () => display.matches || navigator.standalone === true;
  let pending = null;
  let installed = inApp();
  function showInstalled(message) {
    installed = true;
    pending = null;
    button.hidden = true;
    document.querySelector('.app-install-options').hidden = true;
    status.textContent = message;
  }
  if (installed) showInstalled(copy.installed);
  else {
    const ios = /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
    const id = ios ? 'install-ios' : /Android/.test(navigator.userAgent) ? 'install-android' : 'install-desktop';
    document.getElementById(id).open = true;
  }
  window.addEventListener('beforeinstallprompt', function (event) {
    if (installed || inApp()) return;
    event.preventDefault();
    pending = event;
    button.hidden = false;
    button.disabled = false;
    status.textContent = copy.ready;
  });
  button.addEventListener('click', async function () {
    if (!pending) return;
    const prompt = pending;
    pending = null; // A browser install prompt may only be used once.
    button.disabled = true;
    try {
      await prompt.prompt();
      const choice = await prompt.userChoice;
      if (!installed) status.textContent = choice.outcome === 'accepted' ? copy.accepted : copy.dismissed;
    } catch (error) {
      if (!installed) status.textContent = copy.failed;
    } finally {
      button.hidden = !pending || installed;
      button.disabled = false;
    }
  });
  window.addEventListener('appinstalled', () => showInstalled(copy.done));
  if (display.addEventListener) display.addEventListener('change', () => { if (inApp()) showInstalled(copy.installed); });
})();
