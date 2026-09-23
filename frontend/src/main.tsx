import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import './index.css';
// 카드형 개편 화면(fron) 스타일. 결과·리포트 화면은 index.css 클래스를 그대로 쓴다.
import './styles.css';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
