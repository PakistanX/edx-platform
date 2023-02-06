/* global gettext */
/* eslint react/no-array-index-key: 0 */

import PropTypes from 'prop-types';
import React from 'react';
import ReactDOM from 'react-dom';

export function ProgramListing(props) {
  const baseURL = props.baseURL;
  const linkClass = props.linkClass;
  const idBase = props.idBase;

  return (
    <ul className="list-courses">
      {
        props.items.map((item, i) =>
          (
            <li key={i} className="course-item" data-course-key={item.uuid}>
              <a className={linkClass} href={`${baseURL}${item.uuid}`}>
                <h3 className="course-title" id={`title-${idBase}-${i}`}>{item.title}</h3>
                <div className="course-metadata">
                  <span className="course-org metadata-item">
                    <span className="label">{gettext('UUID:')}</span>
                    <span className="value">{item.uuid}</span>
                  </span>
                </div>
              </a>
            </li>
          ),
        )
      }
    </ul>
  );
}

ProgramListing.propTypes = {
  baseURL: PropTypes.string.isRequired,
  idBase: PropTypes.string.isRequired,
  items: PropTypes.arrayOf(PropTypes.object).isRequired,
  linkClass: PropTypes.string.isRequired,
};
