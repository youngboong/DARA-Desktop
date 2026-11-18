import { Menu, Switch } from 'antd';
import styled from 'styled-components';
import { useEffect, useState } from 'react';
import { Link } from 'gatsby';
import React from 'react';
import UpdateDialog from './UpdateDialog';
import { UPDATE_CHECK_MS, showUpdateTag } from './appUpdate.mjs';
import useAdvancedTools from './useAdvancedTools';

const URL = process.env.NODE_ENV === 'production' ? '/api' : 'http://localhost:8001/api';
const PAGES = ['submit', 'results', 'series', 'tutorial'];

const StyledMenu = styled(Menu)`
  border-bottom: none;
`;

// Menu key for the current path; task and compare pages belong to Results.
function keyForPath(pathname) {
  const first = pathname.split('/').filter(Boolean)[0];
  if (!first) return 'submit';
  return ['task', 'compare'].includes(first) ? 'results' : first;
}

function HeaderMenu() {
  const [ selected_key, set_selected_key ] = useState('');
  const [ update, set_update ] = useState(null);
  const [ update_open, set_update_open ] = useState(false);
  const [ advanced, set_advanced ] = useAdvancedTools();

  // Read the path after mount so the server-rendered markup matches the first client render.
  useEffect(() => { set_selected_key(keyForPath(window.location.pathname)); }, []);

  // Version and update state; the server caches the GitHub check for 6 h.
  useEffect(() => {
    const load = () => fetch(`${URL}/update`).then(response => (response.ok ? response.json() : null))
      .then(data => { if (data) set_update(data); }).catch(() => {});
    load();
    const timer = setInterval(load, UPDATE_CHECK_MS);
    return () => clearInterval(timer);
  }, []);

  const handleSelect = ({ key }) => {
    if (PAGES.includes(key)) {  // documentation leaves the app; version and update items open no page
      set_selected_key(key);
    }
  };

  const handleClick = ({ key }) => {
    if (key === 'update' || key === 'check-update') set_update_open(true);
    if (key === 'advanced') set_advanced(!advanced);
  };

  return (
    <>
    <StyledMenu mode='horizontal' 
      activeKey={[selected_key]}
      defaultActiveFirst={[selected_key]}
      selectedKeys={[selected_key]}
      defaultSelectedKeys={[selected_key]}
      onSelect={handleSelect}
      onClick={handleClick}
      inlineCollapsed={false}
      disabledOverflow
    >
      <Menu.Item key='submit' title='Submit'>
        <Link to='/'>Submit</Link>
      </Menu.Item>
      <Menu.Item key='results' title='Results'>
        <Link to='/results'>Results</Link>
      </Menu.Item>
      <Menu.Item key='series' title='Series'>
        <Link to='/series'>Series</Link>
      </Menu.Item>
      {showUpdateTag(update) ? (
        <Menu.Item key='update' title={`DARA ${update.latest} is available`}>
          <span className='dara-update-tag'>Update available</span>
        </Menu.Item>
      ) : null}
      <Menu.SubMenu key='help' title='Help'>
        <Menu.Item key='tutorial' title='Tutorial'>
          <Link to='/tutorial'>Tutorial</Link>
        </Menu.Item>
        <Menu.Item key='doc' title='Documentation'>
          <a href="https://CederGroupHub.github.io/dara/" target="_blank" rel="noopener noreferrer">Documentation</a>
        </Menu.Item>
        <Menu.Divider />
        {/* A per-browser preference: crystallite size, unit cell, RIR, Le Bail and the FWHM curve. */}
        <Menu.Item key='advanced' title='Show the specialist tools: crystallite size and strain, unit cell, RIR, Le Bail fit, instrument FWHM curve'>
          <span className='dara-menu-switch'>Advanced analysis tools
            <Switch size='small' checked={advanced} aria-label='Advanced analysis tools' tabIndex={-1} /></span>
        </Menu.Item>
        {update?.packaged ? <Menu.Item key='check-update'>Check for updates</Menu.Item> : null}
        {update ? <Menu.Item key='version' disabled className='dara-version-item'>{`DARA ${update.current}`}</Menu.Item> : null}
      </Menu.SubMenu>
    </StyledMenu>
    {update?.packaged ? (
      <UpdateDialog open={update_open} onClose={() => set_update_open(false)} status={update} onStatus={set_update} />
    ) : null}
    </>
  )
}

export default HeaderMenu;