import { Menu, Modal, Switch } from 'antd';
import styled from 'styled-components';
import { useEffect, useRef, useState } from 'react';
import { Link, navigate } from 'gatsby';
import { leaveWarning } from './leaveGuard.mjs';
import React from 'react';
import UpdateDialog from './UpdateDialog';
import { UPDATE_CHECK_MS, showUpdateTag } from './appUpdate.mjs';
import useAdvancedTools from './useAdvancedTools';

const URL = process.env.NODE_ENV === 'production' ? '/api' : 'http://localhost:8001/api';

const StyledMenu = styled(Menu)`
  border-bottom: none;
`;

// Menu key for the current path; task and compare pages belong to Results, or to Series when
// opened from a series (`from=series`, or a task's `back` naming one).
export function keyForPath(pathname, search = '') {
  const first = pathname.split('/').filter(Boolean)[0];
  if (!first) return 'submit';
  if (!['task', 'compare'].includes(first)) return first;
  const params = new URLSearchParams(search);
  const back = new URLSearchParams(params.get('back') || '');
  return params.get('from') === 'series' || back.get('from') === 'series' ? 'series' : 'results';
}

/**
 * A header link that asks first when the page holds unsaved work (a loaded pattern, a ranking):
 * client-side route changes do not fire `beforeunload`.
 */
export function GuardedLink({ to, children, ...props }) {
  const onClick = event => {
    const warning = leaveWarning();
    if (!warning || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey) return;
    event.preventDefault();
    Modal.confirm({
      title: 'Leave this page?', content: warning, okText: 'Leave', cancelText: 'Stay',
      onOk: () => navigate(to),
    });
  };
  return <Link to={to} onClick={onClick} {...props}>{children}</Link>;
}

function HeaderMenu() {
  const [ selected_key, set_selected_key ] = useState('');
  const [ update, set_update ] = useState(null);
  const [ update_open, set_update_open ] = useState(false);
  const [ advanced, set_advanced ] = useAdvancedTools();

  // Read the path after mount so the server-rendered markup matches the first client render.
  useEffect(() => { set_selected_key(keyForPath(window.location.pathname, window.location.search)); }, []);
  // Help stays open while its Advanced switch is toggled; Enter on the Help title opens it.
  const [open_keys, set_open_keys] = useState([]);
  const keep_open = useRef(false);

  // Version and update state; the server caches the GitHub check for 6 h.
  useEffect(() => {
    const load = () => fetch(`${URL}/update`).then(response => (response.ok ? response.json() : null))
      .then(data => { if (data) set_update(data); }).catch(() => {});
    load();
    const timer = setInterval(load, UPDATE_CHECK_MS);
    return () => clearInterval(timer);
  }, []);

  const handleClick = ({ key }) => {
    if (key === 'update' || key === 'check-update') set_update_open(true);
    if (key === 'advanced') {
      set_advanced(!advanced);
      keep_open.current = true; // the click's own close of the popup is ignored
      setTimeout(() => { keep_open.current = false; }, 0);
      set_open_keys(['help']);
      return;
    }
    set_open_keys([]);
  };

  return (
    <>
    {/* Uncontrolled active item: rc-menu moves it with the arrow keys (Enter or Down opens Help). */}
    <StyledMenu mode='horizontal'
      // The selected item follows the page (each page mounts the header): a click that the leave
      // guard stops must not mark another page.
      selectedKeys={[selected_key]}
      onClick={handleClick}
      openKeys={open_keys}
      onOpenChange={keys => { if (!keep_open.current) set_open_keys(keys); }}
      inlineCollapsed={false}
      disabledOverflow
    >
      <Menu.Item key='submit' title='Submit'>
        <GuardedLink to='/'>Submit</GuardedLink>
      </Menu.Item>
      <Menu.Item key='results' title='Results'>
        <GuardedLink to='/results/'>Results</GuardedLink>
      </Menu.Item>
      <Menu.Item key='series' title='Series'>
        <GuardedLink to='/series/'>Series</GuardedLink>
      </Menu.Item>
      {showUpdateTag(update) ? (
        <Menu.Item key='update' title={`DARA ${update.latest} is available`}>
          <span className='dara-update-tag'>Update available</span>
        </Menu.Item>
      ) : null}
      <Menu.SubMenu key='help' title='Help' onTitleClick={() => set_open_keys(['help'])}>
        <Menu.Item key='tutorial' title='Tutorial'>
          <GuardedLink to='/tutorial/'>Tutorial</GuardedLink>
        </Menu.Item>
        <Menu.Item key='doc' title='Documentation'>
          <a href="https://CederGroupHub.github.io/dara/" target="_blank" rel="noopener noreferrer">Documentation</a>
        </Menu.Item>
        <Menu.Divider />
        {/* A per-browser preference: crystallite size, unit cell, RIR, Le Bail and the FWHM curve. */}
        <Menu.Item key='advanced' role='menuitemcheckbox' aria-checked={advanced} title='Show the specialist tools: crystallite size and strain, unit cell, RIR, Le Bail fit, instrument FWHM curve'>
          <span className='dara-menu-switch'>Advanced analysis tools
            {/* The menu item takes the keyboard (Enter); the switch only shows the state. */}
            <Switch size='small' checked={advanced} aria-hidden='true' tabIndex={-1} /></span>
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