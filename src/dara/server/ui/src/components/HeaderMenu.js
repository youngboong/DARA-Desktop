import { Menu } from 'antd';
import styled from 'styled-components';
import { useEffect, useState } from 'react';
import { Link } from 'gatsby';
import React from 'react';

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

  // Read the path after mount so the server-rendered markup matches the first client render.
  useEffect(() => { set_selected_key(keyForPath(window.location.pathname)); }, []);

  const handleSelect = ({ key }) => {
    if (key !== 'doc') {  // the documentation link leaves the app
      set_selected_key(key);
    }
  };

  return (
    <StyledMenu mode='horizontal' 
      activeKey={[selected_key]}
      defaultActiveFirst={[selected_key]}
      selectedKeys={[selected_key]}
      defaultSelectedKeys={[selected_key]}
      onSelect={handleSelect}
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
      <Menu.SubMenu key='help' title='Help'>
        <Menu.Item key='tutorial' title='Tutorial'>
          <Link to='/tutorial'>Tutorial</Link>
        </Menu.Item>
        <Menu.Item key='doc' title='Documentation'>
          <a href="https://CederGroupHub.github.io/dara/" target="_blank" rel="noopener noreferrer">Documentation</a>
        </Menu.Item>
      </Menu.SubMenu>
    </StyledMenu>
  )
}

export default HeaderMenu;