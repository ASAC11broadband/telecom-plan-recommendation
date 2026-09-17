import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    // host: true → 같은 와이파이의 폰에서 http://<PC IP>:5173 으로 접속 가능.
    // /api 프록시는 개발 PC 안에서 도니까 백엔드는 127.0.0.1 에만 떠 있으면 되고 CORS 도 불필요.
    host: true,
    // 백엔드가 다른 포트에 떠 있으면 API_TARGET 으로 바꾼다.
    // (기존 서버를 끄지 않고 다른 브랜치를 같이 띄워 볼 때 쓴다)
    proxy: { '/api': process.env.API_TARGET || 'http://127.0.0.1:8000' },
  },
});
