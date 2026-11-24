import { Modal } from 'antd';
import { navigate } from 'gatsby';
import { guardHistory } from './src/components/leaveGuard.mjs';

// Runs before the router listens for popstate, so Back can be held while a page has unsaved work.
export const onClientEntry = () => {
  guardHistory((content, leave) => Modal.confirm({ title: 'Leave this page?', content, okText: 'Leave', cancelText: 'Stay', onOk: leave }), navigate);
};
