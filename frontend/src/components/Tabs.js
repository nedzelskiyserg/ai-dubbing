import React, { useContext } from 'react';
import './Tabs.css';
import { AppContext } from '../AppContext';

const Tabs = () => {
  const { activePage, setActivePage } = useContext(AppContext);

  const tabs = [
    { id: 'youtube-dubbing', label: 'YOUTUBE DUBBING' },
    { id: 'shorts-generator', label: 'SHORTS GENERATOR' },
  ];

  return (
    <div className="tabs-container">
      {tabs.map(tab => (
        <button
          key={tab.id}
          className={`tab-item ${activePage === tab.id ? 'active' : ''}`}
          onClick={() => setActivePage(tab.id)}
        >
          <span className="tab-indicator"></span>
          <span className="tab-label">{tab.label}</span>
        </button>
      ))}
    </div>
  );
};

export default Tabs;
