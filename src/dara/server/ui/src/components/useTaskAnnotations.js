import React from 'react';
import { message } from 'antd';
import { API } from './QuickRefineProgress';

// One list per task, shared by every plot of the task on the page (preview and fit workspace).
const lists = new Map();
const listeners = new Map();
const publish = (taskId, list) => {
  lists.set(taskId, list);
  (listeners.get(taskId) || new Set()).forEach(listener => listener(list));
};

/**
 * `[annotations, save]` of a task: the analyst's plot labels (GET /task/{id}/annotations, [] when
 * the server has none or no such endpoint). `save(list)` shows the new list at once and PUTs it;
 * on failure the previous list comes back with an error message.
 */
export default function useTaskAnnotations(taskId) {
  const [list, setList] = React.useState(() => lists.get(taskId) || []);
  React.useEffect(() => {
    if (taskId === undefined || taskId === null) return undefined;
    const own = listeners.get(taskId) || new Set();
    const first = own.size === 0;
    listeners.set(taskId, own.add(setList));
    // Another task's labels never stay on screen while this one's load.
    setList(lists.get(taskId) || []);
    // The first plot of the task on the page loads the list again: the cache only shares it between
    // plots shown together (a colleague or another tab may have changed it since).
    if (first) {
      fetch(`${API}/task/${taskId}/annotations`)
        .then(response => (response.ok ? response.json() : null))
        .then(body => { if (body) publish(taskId, Array.isArray(body.annotations) ? body.annotations : []); })
        .catch(() => {});
    }
    return () => { own.delete(setList); };
  }, [taskId]);
  const save = React.useCallback(async next => {
    const previous = lists.get(taskId) || [];
    publish(taskId, next);
    try {
      const response = await fetch(`${API}/task/${taskId}/annotations`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ annotations: next }),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(typeof body.detail === 'string' ? body.detail : `HTTP ${response.status}`);
      }
    } catch (failure) {
      publish(taskId, previous);
      message.error(`The annotation could not be saved: ${failure.message}`);
    }
  }, [taskId]);
  return [list, save];
}
