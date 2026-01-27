import React, { useState, useRef, useEffect } from 'react';
import { createPortal } from 'react-dom';
import './Dropdown.css';

const Dropdown = ({ 
  value, 
  onChange, 
  options, 
  className = '', 
  placeholder = 'Select...',
  disabled = false 
}) => {
  const [isOpen, setIsOpen] = useState(false);
  const [selectedValue, setSelectedValue] = useState(value);
  const dropdownRef = useRef(null);
  const triggerRef = useRef(null);
  const menuRef = useRef(null);
  const [menuPosition, setMenuPosition] = useState({ top: 0, left: 0, width: 0 });

  useEffect(() => {
    setSelectedValue(value);
  }, [value]);

  // Вычисляем позицию меню относительно триггера
  const updateMenuPosition = () => {
    if (triggerRef.current) {
      const triggerRect = triggerRef.current.getBoundingClientRect();
      setMenuPosition({
        top: triggerRect.bottom + 4 + window.scrollY,
        left: triggerRect.left + window.scrollX,
        width: triggerRect.width
      });
    }
  };

  useEffect(() => {
    if (isOpen) {
      updateMenuPosition();
      
      // Обновляем позицию при скролле и ресайзе
      window.addEventListener('scroll', updateMenuPosition, true);
      window.addEventListener('resize', updateMenuPosition);
      
      return () => {
        window.removeEventListener('scroll', updateMenuPosition, true);
        window.removeEventListener('resize', updateMenuPosition);
      };
    }
  }, [isOpen]);

  useEffect(() => {
    const handleClickOutside = (event) => {
      if (
        dropdownRef.current && 
        !dropdownRef.current.contains(event.target) &&
        menuRef.current &&
        !menuRef.current.contains(event.target)
      ) {
        setIsOpen(false);
      }
    };

    const handleWheel = (event) => {
      // Блокируем скролл страницы когда dropdown открыт
      // Блокируем везде, включая элементы меню
      if (isOpen) {
        event.preventDefault();
        event.stopPropagation();
        return false;
      }
    };

    const handleTouchMove = (event) => {
      // Блокируем touch скролл когда dropdown открыт
      // Блокируем везде, включая элементы меню
      if (isOpen) {
        event.preventDefault();
        event.stopPropagation();
        return false;
      }
    };

    if (isOpen) {
      document.addEventListener('mousedown', handleClickOutside);
      document.addEventListener('wheel', handleWheel, { passive: false, capture: true });
      document.addEventListener('touchmove', handleTouchMove, { passive: false, capture: true });
      
      // Блокируем скролл страницы
      const originalOverflow = document.body.style.overflow;
      const originalPosition = document.body.style.position;
      const originalTop = document.body.style.top;
      const scrollY = window.scrollY;
      
      document.body.style.overflow = 'hidden';
      document.body.style.position = 'fixed';
      document.body.style.top = `-${scrollY}px`;
      document.body.style.width = '100%';
      
      return () => {
        document.removeEventListener('mousedown', handleClickOutside);
        document.removeEventListener('wheel', handleWheel, { capture: true });
        document.removeEventListener('touchmove', handleTouchMove, { capture: true });
        
        document.body.style.overflow = originalOverflow;
        document.body.style.position = originalPosition;
        document.body.style.top = originalTop;
        document.body.style.width = '';
        window.scrollTo(0, scrollY);
      };
    }
  }, [isOpen]);

  const handleSelect = (optionValue) => {
    setSelectedValue(optionValue);
    onChange(optionValue);
    setIsOpen(false);
  };

  const selectedOption = options.find(opt => opt.value === selectedValue) || options[0];
  const displayText = selectedOption ? selectedOption.label : placeholder;

  const handleMenuWheel = (e) => {
    // Блокируем скролл страницы полностью
    e.stopPropagation();
    e.preventDefault();
    
    // Разрешаем скролл только внутри меню программно
    const menu = e.currentTarget;
    const { scrollTop, scrollHeight, clientHeight } = menu;
    const isAtTop = scrollTop === 0;
    const isAtBottom = scrollTop + clientHeight >= scrollHeight - 1;
    
    // Если не достигли границ - скроллим меню вручную
    if (!((e.deltaY < 0 && isAtTop) || (e.deltaY > 0 && isAtBottom))) {
      menu.scrollTop += e.deltaY;
    }
  };

  const handleWrapperWheel = (e) => {
    // Блокируем скролл страницы когда dropdown открыт
    // Блокируем везде, включая элементы меню
    if (isOpen) {
      e.stopPropagation();
      e.preventDefault();
    }
  };

  return (
    <>
      <div 
        className={`dropdown-wrapper ${className} ${isOpen ? 'open' : ''} ${disabled ? 'disabled' : ''}`}
        ref={dropdownRef}
        onWheel={handleWrapperWheel}
        onTouchMove={(e) => {
          // Блокируем touch скролл когда dropdown открыт
          if (isOpen) {
            e.stopPropagation();
            e.preventDefault();
          }
        }}
      >
        <div 
          ref={triggerRef}
          className="dropdown-trigger"
          onClick={() => !disabled && setIsOpen(!isOpen)}
        >
          <span className="dropdown-value">{displayText}</span>
          <span className="dropdown-arrow">▼</span>
        </div>
      </div>
      {isOpen && createPortal(
        <div 
          ref={menuRef}
          className="dropdown-menu"
          style={{
            position: 'absolute',
            top: `${menuPosition.top}px`,
            left: `${menuPosition.left}px`,
            width: `${menuPosition.width}px`
          }}
          onWheel={handleMenuWheel}
          onTouchMove={(e) => {
            // Блокируем touch скролл страницы
            e.stopPropagation();
          }}
        >
          {options.map((option, index) => (
            <div
              key={option.value || index}
              className={`dropdown-option ${selectedValue === option.value ? 'selected' : ''}`}
              onClick={() => handleSelect(option.value)}
            >
              {option.label}
            </div>
          ))}
        </div>,
        document.body
      )}
    </>
  );
};

export default Dropdown;
