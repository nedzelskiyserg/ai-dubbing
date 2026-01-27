import React from 'react';
import './Header.css';
import { openFolder } from '../api';

const Header = () => {
  const handleFolderClick = async () => {
    try {
      await openFolder();
    } catch (error) {
      console.error('Failed to open folder:', error);
    }
  };

  return (
    <header className="header">
      <div className="header-left">
        <div className="logo-block">
          <span className="logo-text">AI</span>
        </div>
        <div className="title-group">
          <div className="title-label">[SYS:DUBBING]</div>
          <div className="title-text">AI DUBBING STUDIO</div>
        </div>
      </div>
      <div className="header-right">
        <button className="folder-btn" onClick={handleFolderClick}>
          <span className="material-symbols-outlined">folder_open</span>
        </button>
      </div>
    </header>
  );
};

export default Header;
