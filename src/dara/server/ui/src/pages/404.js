import React from 'react';
import { Button } from 'antd';
import Layout from '../components/Layout';

function NoFoundPage() {
  const [path, setPath] = React.useState('');
  // Read after mount, so the server-rendered page and the first client render agree.
  React.useEffect(() => { setPath(window.location.pathname); }, []);
  return (
    <Layout hasSider={false} title="Page not found">
      <div className="dara-empty" role="alert">
        <h3>Page not found</h3>
        <p>{path ? <><code>{path}</code> does not exist in DARA. </> : 'This page does not exist in DARA. '}
          The address may be incomplete, or the task or series it pointed to was deleted.</p>
        <div className="dara-empty-actions">
          <Button type="primary" href="/results/">Results</Button>
          <Button href="/">Submit a pattern</Button>
        </div>
      </div>
    </Layout>
  );
}

export default NoFoundPage;
