import React, { useEffect } from 'react';
import { Layout as LayoutAntd } from 'antd';
import styled from 'styled-components';
import HeaderMenu from './HeaderMenu';
import { Link } from 'gatsby';
import Halmet from 'react-helmet';
import favicon from '../images/favicon.ico';
import { startPresence } from './presence.mjs';

import 'antd/dist/antd.css';
import './tokens.css';
import './layout.css';

const BREAK_POINT = 768;

const {
  Header: HeaderAntd,
  Sider: SiderAntd,
  Content: ContentAntd,
} = LayoutAntd;

function Header({ current }) {
  return (
    <HeaderAntd>
      <div style={{ display: 'flex', justifyContent: 'space-between' }}>
        <Link className='page-title' to='/' aria-label='DARA, new analysis'>
          {/* Mark: a reference stick pattern on its baseline, the line list every phase card carries. */}
          <svg className='page-title-mark' viewBox='0 0 24 24' width='22' height='22' aria-hidden='true'>
            <path d='M5 19V12M9 19V5M13 19V14M17 19V8M21 19V15' fill='none' stroke='var(--dara-shell-mark)' strokeWidth='2' strokeLinecap='square' />
            <path d='M2 21H23' fill='none' stroke='currentColor' strokeWidth='1.5' strokeLinecap='square' />
          </svg>
          <span className='page-title-text'>DARA</span>
          <span className='page-title-sub'>XRD phase analysis</span>
        </Link>
        <HeaderMenu current={current}></HeaderMenu>
      </div>
    </HeaderAntd>
  )
}

function Sider() {
  return (<></>)
}

/** A render error shows the header, one line and a way back instead of a white page. */
class ErrorBoundary extends React.Component {
  constructor(props) { super(props); this.state = { error: null }; }
  static getDerivedStateFromError(error) { return { error }; }
  componentDidCatch(error) { console.error(error); }
  render() {
    if (!this.state.error) return this.props.children;
    return <div role="alert" className="dara-empty">
      <h3>This page could not be displayed</h3>
      <p>{String(this.state.error?.message || this.state.error).split('\n')[0]}</p>
      <Link to="/results/">Back to Results</Link>
    </div>;
  }
}

const LayoutContainer = styled(LayoutAntd)`
  background: var(--dara-ground);
  min-height: 100vh;

  .ant-layout-header {
    padding: 0 clamp(16px, 2vw, 48px);
    margin-bottom: 8px;
    height: 48px;
    line-height: 48px;
    background: var(--dara-shell);
  }

  .ant-layout-header .ant-menu-horizontal {
    line-height: 48px;
    background: transparent;
    color: var(--dara-shell-text-dim);
  }

  .ant-layout-header .ant-menu-horizontal > .ant-menu-item a,
  .ant-layout-header .ant-menu-horizontal > .ant-menu-submenu .ant-menu-submenu-title {
    color: var(--dara-shell-text-dim);
  }

  .ant-layout-header .ant-menu-horizontal > .ant-menu-item:hover a,
  .ant-layout-header .ant-menu-horizontal > .ant-menu-item-selected a,
  .ant-layout-header .ant-menu-horizontal > .ant-menu-submenu:hover .ant-menu-submenu-title,
  .ant-layout-header .ant-menu-horizontal > .ant-menu-submenu-selected .ant-menu-submenu-title {
    color: var(--dara-shell-text);
  }

  .ant-layout-header .ant-menu-horizontal > .ant-menu-item::after,
  .ant-layout-header .ant-menu-horizontal > .ant-menu-submenu::after {
    border-bottom-width: 3px;
  }

  .ant-layout-header .ant-menu-horizontal > .ant-menu-item-selected::after,
  .ant-layout-header .ant-menu-horizontal > .ant-menu-item:hover::after,
  .ant-layout-header .ant-menu-horizontal > .ant-menu-submenu:hover::after,
  .ant-layout-header .ant-menu-horizontal > .ant-menu-submenu-selected::after {
    border-bottom-color: var(--dara-shell-mark);
  }

  .page-title {
    display: inline-flex;
    align-items: center;
    gap: 10px;
    height: 48px;
    color: var(--dara-shell-text);
  }

  .page-title-mark {
    flex: none;
  }

  .page-title-text {
    font-size: 17px;
    font-weight: 700;
    line-height: 1;
    letter-spacing: 0.14em;
  }

  /* Descriptor set off by a hairline, in the shell's own dim tint. */
  .page-title-sub {
    padding-left: 12px;
    border-left: 1px solid rgba(247, 248, 245, 0.28);
    font-size: var(--dara-font-12);
    line-height: 16px;
    letter-spacing: 0.02em;
    color: var(--dara-shell-text-dim);
    white-space: nowrap;
  }

  .page-title:hover {
    color: var(--dara-shell-text);
  }

  .page-title:hover .page-title-sub {
    color: var(--dara-shell-text);
  }

  .ant-layout-footer {
    background: var(--dara-ground);
  }

  .ant-layout-sider {
    padding: 16px 24px;
  }

  .ant-layout-content {
    padding: 16px clamp(16px, 2vw, 48px);
    min-width: 0;
  }

  .ant-layout-sider {
    transition: none;
  }

  .ant-divider-horizontal {
    margin: 8px 0;
  }

  .ant-menu-title-content {
    font-weight: 500;
  }

  .sider-container {
    display: block;
  }

  .sider-container-collapsed {
    display: none;
  }

  @media (max-width: ${BREAK_POINT}px) {
    .main-container {
      flex-direction: column !important;
    }

    .ant-layout-sider,
    .ant-layout-content {
      padding: 4px 16px;
    }

    .ant-layout-sider {
      width: 100% !important;
      max-width: 100% !important;
      min-width: 100% !important;
      flex: unset !important;
    }

    .main-container > .ant-layout-content {
      width: 100% !important;
    }

    .sider-container {
      display: none !important;
    }
  
    .sider-container-collapsed {
      display: block !important;
    }
  }

  @media (max-width: 568px) {
    .page-title-sub {
      display: none;
    }

    .page-title {
      margin-right: 8px;
    }

    .ant-layout-header .ant-menu-horizontal > .ant-menu-item,
    .ant-layout-header .ant-menu-horizontal > .ant-menu-submenu {
      padding: 0 8px;
    }
  }
`;

const ContentContainer = styled(LayoutAntd)`
  background: var(--dara-ground);
  flex: 1 0 auto;
  width: 100%;
  max-width: 1920px;
  margin: 0 auto;
`;

function Layout({ children, hasSider, title }) {
  useEffect(() => { startPresence(); }, []);
  return (
      <LayoutContainer className="layout-container">
        <Halmet>
          <title>{title ? `${title} | `: ""}DARA | Data-driven automated Rietveld analysis for phase search and refinement</title>
        </Halmet>
        <Halmet>
          <link rel="icon" href={favicon} />
        </Halmet>
        <Halmet>
          <meta charset="utf-8" />
        </Halmet>
        <Halmet>
          <meta http-equiv="X-UA-Compatible" content="chrome=1" />
        </Halmet>
        <Halmet>
          <meta name="keywords" content="DARA, XRD, Rietveld refinement, phase identification" />
        </Halmet>
        <Halmet>
          <meta name="subject" content="XRD phase identification and Rietveld refinement" />
        </Halmet>

        <Header></Header>
        <ContentContainer className='main-container'>
          {hasSider ? <Sider /> : null}
          <ContentAntd>
            <ErrorBoundary>{children}</ErrorBoundary>
          </ContentAntd>
        </ContentContainer>
      </LayoutContainer>
  )
}

export { BREAK_POINT };
export default Layout;