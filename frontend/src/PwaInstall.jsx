import React, { useEffect, useId, useRef, useState } from "react";
import {
  ArrowDownToLine,
  Check,
  Monitor,
  Smartphone,
  Wifi,
  X,
} from "lucide-react";
import { promptInstall, usePwa } from "./pwa";
import "./pwa.css";

function InstallGuide({ onClose }) {
  const { prompt } = usePwa();
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const ref = useRef(null);
  const headingId = useId();
  useEffect(() => {
    const previous = document.activeElement;
    const dialog = ref.current;
    dialog.showModal();
    return () => {
      dialog.close();
      if (previous?.isConnected) previous.focus();
    };
  }, []);
  return (
    <dialog
      ref={ref}
      className="pwa-dialog"
      aria-labelledby={headingId}
      onCancel={(e) => {
        e.preventDefault();
        onClose();
      }}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="pwa-dialog-content">
        <button
          type="button"
          className="icon-btn pwa-close"
          aria-label="닫기"
          onClick={onClose}
        >
          <X size={19} />
        </button>
        <img
          className="pwa-app-icon"
          src="/icons/notedown.svg"
          width="60"
          height="60"
          alt=""
        />
        <span className="pwa-eyebrow">YOUR WORKSPACE, ONE TAP AWAY</span>
        <h2 id={headingId}>Notedown 앱 설치</h2>
        <p className="pwa-description">
          홈 화면이나 Dock에서
          <br />
          나만의 문서 공간을 바로 열어보세요.
        </p>
        {!window.isSecureContext ? (
          <div className="pwa-connection-note" role="status">
            <Wifi size={18} />
            <p>
              앱을 설치하려면 HTTPS 주소로 접속해 주세요. 같은 기기의
              localhost에서도 설치할 수 있어요.
            </p>
          </div>
        ) : (
          <>
            {prompt && (
              <button
                type="button"
                className="btn primary pwa-native-install"
                disabled={busy}
                onClick={async () => {
                  setBusy(true);
                  setMessage("");
                  try {
                    if (await promptInstall()) onClose();
                  } catch {
                    setMessage(
                      "설치 창을 열지 못했어요. 아래 브라우저 메뉴를 이용해 주세요.",
                    );
                  } finally {
                    setBusy(false);
                  }
                }}
              >
                <ArrowDownToLine size={17} />
                지금 설치
              </button>
            )}
            <div className="pwa-install-methods">
              <div>
                <Monitor size={19} />
                <p>
                  <strong>Chrome · Edge</strong>
                  <span>
                    주소창의 설치 아이콘 또는 브라우저 메뉴에서 ‘앱 설치’를
                    선택하세요.
                  </span>
                </p>
              </div>
              <div>
                <Smartphone size={19} />
                <p>
                  <strong>iPhone · iPad</strong>
                  <span>공유 메뉴에서 ‘홈 화면에 추가’를 선택하세요.</span>
                </p>
              </div>
              <div>
                <Monitor size={19} />
                <p>
                  <strong>Mac Safari</strong>
                  <span>파일 메뉴에서 ‘Dock에 추가’를 선택하세요.</span>
                </p>
              </div>
            </div>
          </>
        )}
        {message && (
          <p className="pwa-message" role="status">
            {message}
          </p>
        )}
        <p className="pwa-online-note">
          <Check size={14} />
          문서 열기와 저장에는 서버 연결이 필요해요.
        </p>
      </div>
    </dialog>
  );
}

export default function PwaInstall({ className = "" }) {
  const { installed } = usePwa();
  const [open, setOpen] = useState(false);
  if (installed) return null;
  return (
    <>
      <button
        type="button"
        className={`pwa-install-button ${className}`}
        onClick={() => setOpen(true)}
      >
        <ArrowDownToLine size={17} />
        <span>앱 설치</span>
      </button>
      {open && <InstallGuide onClose={() => setOpen(false)} />}
    </>
  );
}
